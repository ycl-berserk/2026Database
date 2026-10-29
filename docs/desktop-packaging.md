# 桌面打包与个人模式

> 状态：Phase 0 可行性、Phase 1 桌面运行时、Phase 2 Windows 打包、Phase 3 局域网共享、Phase 4 macOS 打包脚本与 CI、Phase 5 文档与发布准备完成（0.2.0）；macOS 尚未人工验收。
>
> 目标：把同一套 FastAPI + PostgreSQL 应用打成 Windows / macOS 安装包，每个用户的数据只存在本机；演示模式（Docker + 源码）保持不变。

## 1. 目标架构

安装目录存放只读资源：

```
resources/
  pgsql/                 # 裁剪后的 PostgreSQL 16 + pg_trgm + pgvector
    bin/ lib/ share/
  sidecar/
    ls-server.exe        # PyInstaller onedir
    _internal/
  sql/                   # schema / views / migrations / roles
```

用户数据目录 `LS_HOME`（Tauri `app_local_data_dir()`）：

- Windows：`%LOCALAPPDATA%\com.learningspace.desktop`
- macOS：`~/Library/Application Support/com.learningspace.desktop`

子目录为 `pgdata/`、`logs/`、`backups/`、`run/`，文件为 `config.json`、`.env`。应用端口默认 8765，PostgreSQL 默认 55432；首次取空闲端口并持久化。PG 只监听 `127.0.0.1`，使用 `scram-sha-256`。

## 2. Phase 0 实测数据（Windows 11 x64，2026-10-29）

| 项目 | 结果 |
| --- | --- |
| EDB PostgreSQL 归档 | 322,530,154 字节（307.5 MiB），SHA-256 固定 |
| 解压（仅 bin/lib/share/include） | 138,896,213 字节（132.5 MiB） |
| 裁剪后 PG | 62,936,822 字节（60.0 MiB） |
| PyInstaller onedir sidecar | 43,433,680 字节（41.4 MiB）；排除 PIL/setuptools 前 58 MiB |
| 安装后资源合计 | 约 106 MB（101.4 MiB，不含用户数据） |
| `initdb` + `pg_ctl start` | 8.0 s |
| 建库 + 迁移 + 视图 + 角色 | 0.94 s |
| sidecar 启动到 `/healthz` 200 | 0.18–0.66 s（冷/热缓存） |
| 空库 `pg_dump -Fc` | 98 KiB |

结论：资源体积远低于“安装后 ≤350 MB”预算；按当前体积，NSIS/DMG 压缩后预计明显低于 120 MB / 150 MB 的目标。pgvector 0.8.0 在 EDB 头文件 + MSVC 2022 下构建通过，`pg_trgm` 与 `vector` 均可 `CREATE EXTENSION`，现有 `schema.sql`、9 个迁移、15 个视图和角色权限验证全部通过。

## 3. 关键发现：PostgreSQL 不接受中文可执行路径

在包含 `大作业` 的仓库路径下直接运行裁剪后的 `initdb.exe`，初始 bootstrap 成功，但 post-bootstrap 报：

```
FATAL: invalid byte sequence for encoding "UTF8": 0xb4
```

把同一份二进制放到 `C:\ls-pgtest\pgsql`（ASCII）后 `initdb` 成功。因此：

- 构建机、CI 和打包临时目录必须使用 ASCII 路径。
- 桌面运行时不能假定安装目录/用户名是 ASCII，必须把转发后的 ASCII 视图交给 PostgreSQL，禁止直接把含中文的资源路径传给 `initdb`/`pg_ctl`。
- 安装包 `productName` 保持英文 `Learning Space`，但这不是充分条件，仍需运行时处理。

Windows CI（英文系统，ACP=1252）上进一步实测：

| 场景 | 结果 |
| --- | --- |
| 中文安装路径直接调用 `initdb` | 失败 |
| ASCII 路径 + junction 指向 ASCII 目标 | 成功 |
| ASCII 路径 + junction 指向中文目标 | 失败 |
| ASCII 路径 + `subst` 盘符指向中文目标 | 成功 |
| `subst` 盘符指向中文安装目录（PG 可执行文件本身经盘符访问） | 成功 |

原因是 Windows 版 PostgreSQL 用 ANSI 代码页（`CP_ACP`）转换路径：`pgreadlink()`（`src/port/dirmod.c`）解析 junction 时以 `CP_ACP` 把 UTF-16 目标转成 ANSI，英文系统下中文退化为 `?`；随后 `_pgstat64()`（`src/port/win32stat.c`）re-stat 该目标，在 `SelectConfigFiles()` 里报 `could not access directory ... No such file or directory`。`subst` 盘符由对象管理器解析，不经过 `CP_ACP`，所以能稳定转发。运行时因此统一使用 `subst`：

- 数据目录非 ASCII 时映射为 `<盘符>:\`；安装目录（`resources/pgsql`）非 ASCII 时同样映射，PG 可执行文件、share、lib 都经盘符访问。
- PG 工具的命令行参数同样按 ANSI 解析（`pg_dump --file` 会退化成 `?`），所以 sidecar 全程使用盘符视图；`configure_environment` 用 `os.path.abspath` 而非 `Path.resolve()`，避免把 `subst` 盘符还原回真实中文路径。
- 盘符选择 `D:`–`Z:` 倒序的空闲盘符；复用已有映射前先校验标记文件（`run/.ascii-drive` 哈希）与 `postgres.exe` 大小，退出时对所有本次使用过的盘符执行 `subst /D`。

## 4. 桌面运行时（Phase 1）

`app/desktop.py` 提供 `ls-server` 子命令，打包前可直接用 `python -m app.desktop` 调试：

| 命令 | 作用 |
| --- | --- |
| `init-env` | 创建 `LS_HOME` 子目录，生成 `.env`（密钥、端口、连接串） |
| `bootstrap` | 设置 postgres 密码 → 建主库 → schema → 迁移 → 视图 → 角色密码覆盖 |
| `serve` | 启动 FastAPI；默认 `127.0.0.1`，端口取 `LS_PORT` |
| `backup` / `restore` | 用内置 `pg_dump` / `pg_restore`（`--pg-bin`），默认保留 7 份 |
| `doctor` | 检查目录、密钥文件、pg 工具、主库/只读库连接与待执行迁移 |
| `smoke` | 初始化临时目录、起服务、请求 `/healthz` 与 `/setup`、备份后退出 |

环境变量：`LS_HOME`、`LS_ENV_FILE`、`LS_DATA_DIR`、`LS_DESKTOP_MODE`、`LS_ALLOW_INSECURE_HTTP`、`LS_BIND_HOST`、`LS_PG_PORT`、`LS_PORT`、`LS_PG_BIN`，以及标准的 `DATABASE_URL` / `T2S_DATABASE_URL` / `ADMIN_DATABASE_URL` / `SESSION_SECRET`。

行为约定：

- `.env` 只在首次生成；`LS_APP_PASSWORD`、`LS_T2S_PASSWORD`、`LS_PG_ADMIN_PASSWORD`、`SESSION_SECRET` 均为随机值，Unix 下 0600；`sql/roles.sql` 里的固定开发密码随后被 `ALTER ROLE` 覆盖。
- `APP_ENV=production`：登录页隐藏演示账号，密钥长度校验照常生效。
- **明文 HTTP 例外仅限桌面模式**：只有同时设置 `LS_DESKTOP_MODE=1` 与 `LS_ALLOW_INSECURE_HTTP=1` 才关闭 Secure Cookie 与 HSTS；非桌面环境带该标志会直接启动失败。
- `/setup` 仅在桌面模式且 `app.users` 为空时开放；创建的用户直接是管理员并自动登录。`/` 在未初始化时跳 `/setup`，已初始化跳 `/dashboard`。
- `/healthz` 增加 `desktop` 与 `initialized` 字段，Tauri 壳用它决定打开 `/setup` 还是登录页。
- 启动时每日首次备份、迁移前强制备份；迁移仍走 `app/migrations.py`，`scripts/init_db.py` 保留为演示模式薄 CLI。

2026-10-29 用裁剪后的 PostgreSQL 16.10 + `python -m app.desktop` 做了端到端验证：`bootstrap` 建库并写入 16 张表、15 个视图，`doctor` 全部通过，`serve` 后 `/healthz` 返回 `initialized:false`，完成 `/setup` 后变为 `true`，`/setup` 随后 303 到 `/login`，`backup` 生成约 97 KiB 的空库 dump，`smoke` 一次通过。

## 5. 构建步骤（当前脚本）

```powershell
# 0) 依赖
uv sync
uv pip install -r packaging/requirements.txt

# 1) 下载/裁剪 PG、构建 pgvector、打包 sidecar、汇总 resources
uv run python packaging/build.py
#    可分别跳过：--skip-fetch --skip-pgvector --skip-sidecar --clean-sidecar

# 2) 生成 NSIS 安装包
cd desktop
npm install
npm run build          # tauri build --bundles nsis
```

`packaging/build.py` 按顺序执行：`fetch_trim.py --stage all` → `build_pgvector.py` →
PyInstaller `ls-server.spec` → 复制 `pgsql/`、`sidecar/`、`sql/` 到
`desktop/src-tauri/resources/`，写入 `version.json`，并输出
`packaging/build/size-report.{json,md}`；在 GitHub Actions 中同时追加到 Step Summary。
每个阶段都以产物存在为幂等条件，CI 缓存 `packaging/build/pgsql` 与 `packaging/.cache`。

构建工具版本固定在 `packaging/requirements.txt`：PyInstaller 6.22.3、pefile 2024.8.26。`fetch_trim.py` 会删除 `include/doc/pgAdmin/StackBuilder`，排除静态 `.lib`，再按 PE 导入闭包删除未使用的 DLL（pgAdmin 的 wxWidgets、libcurl 等），只保留服务端、客户端工具及其依赖。

Tauri 配置（`desktop/src-tauri/tauri.conf.json`）：

- `productName: "Learning Space"`，窗口标题仍为中文“学习空间”。
- `bundle.resources: ["resources/**/*"]`，安装后位于 `<安装目录>/resources/{pgsql,sidecar,sql}`。
- `bundle.targets: ["nsis"]`；NSIS 使用 `currentUser`、简体中文/英文、不显示语言选择器。
- WebView2 使用 `downloadBootstrapper`，缺失时由安装器下载。

macOS 使用平台覆盖文件 `tauri.macos.conf.json`：`targets: ["app", "dmg"]`、
`minimumSystemVersion: "13.0"`、图标加入 `icons/icon.icns`（由 `icons/icon.png`
生成 16–1024 全部尺寸）。`tauri.conf.json` 本身保持 Windows 默认值。

### 5.1 macOS 构建（Phase 4）

macOS 侧没有可用的 arm64 PostgreSQL 预编译包，因此打包时从源码构建：

| 步骤 | 脚本 | 说明 |
| --- | --- | --- |
| PostgreSQL 16.10 | `packaging/postgres/build_macos.sh --prefix packaging/build/pgsql` | 下载固定 SHA-256 的官方源码包，`--without-icu/-readline/-libxml/-lz4/-zstd --disable-nls` 最小化配置，`make install` 后安装 contrib `pg_trgm`、`pg_stat_statements` |
| pgvector 0.8.0 | `packaging/postgres/build_pgvector_macos.sh --prefix …` | 用构建出的 `pg_config` 编译，安装到同一 prefix |
| 裁剪 | `packaging/build.py:trim_macos()` | 删除 `include/`、`share/doc|man`、`lib/pgxs|pkgconfig`，`bin/` 只保留 `postgres initdb pg_ctl pg_isready psql pg_dump pg_restore pg_config` |
| 依赖重定位 | `packaging/build.py:relocate_macos()` | PostgreSQL 的 dylib `install_name` 是构建时的绝对路径，安装到 `.app` 后失效；用 `install_name_tool` 改为 `@rpath/…`，可执行文件加 `@executable_path/../lib`（扩展加 `@loader_path/..`）。`postgres` 自己通过 `make_relative_path` 定位 share/lib，无需处理 |

`packaging/build.py` 在 darwin 上自动选择上述脚本，产物布局与 Windows 完全一致
（`pgsql/{bin,lib,share}`），因此 `paths.rs` 无需平台分支。副产物记录在
`packaging/postgres/manifest-macos.json`（版本、URL、SHA-256、configure 参数）。

签名：MVP 无 Apple Developer 证书，CI 对 app 内所有可执行文件、`*.dylib`、`*.so`
逐个 `codesign --force --sign -`，再签 `.app`，最后 `codesign --verify --deep --strict`。
DMG 用 `hdiutil` 从签名后的 app 生成（附 `/Applications` 快捷方式）。未签名/未公证
的包在 Gatekeeper 下需要右键“打开”或 `xattr -dr com.apple.quarantine`，正式分发需
证书与公证。

## 6. Sidecar 打包要点

- 使用 onedir，不使用 onefile/UPX：启动更快、杀软误报更少，且保留 `_internal`。
- `ls-server.spec` 打包 `app/templates`、`app/static`、`sql` 到 `_internal` 下对应位置，保证 `settings.base_dir` 的源路径与冻结路径一致。
- 排除 pytest、hypothesis、numpy、pandas、PIL、pptx、setuptools 等非运行时依赖；PIL 曾因开发环境被误收集，占用 13 MiB。
- `uvicorn`、`psycopg[binary]`、`sqlglot` 已通过实际启动与 `/healthz`、`/login`、静态资源请求验证。
- `packaging/sidecar/entry.py` 现在只做两件事：`--version` 输出版本；其余参数转交 `app.desktop.main`，因此安装包内的 `ls-server` 与源码 `python -m app.desktop` 行为一致。
- 第三方许可汇总见 `packaging/licenses/THIRD_PARTY_NOTICES.md`。

## 7. Windows 桌面壳（Phase 2）

Rust 侧拆分为：

| 模块 | 职责 |
| --- | --- |
| `paths.rs` | 解析 `resources/`、`LS_HOME`、sidecar/PG 路径；非 ASCII 的数据目录与安装目录通过 `subst` 盘符转发，退出时释放 |
| `config.rs` | `config.json`（端口、LAN 开关）、`run/state.json`、空闲端口扫描、残留 sidecar 清理 |
| `postgres.rs` | `initdb`、`postgresql.conf` 受管块、`pg_ctl start/stop/status` |
| `server.rs` | 调用 `ls-server init-env/bootstrap/serve/backup`、健康检查、日志重定向 |
| `smoke.rs` | `--smoke` 无界面自检，输出 `smoke-report.json` |
| `lib.rs` | 单实例、托盘、快捷键、剪贴板、启动编排与退出清理 |

启动顺序：

1. 单实例插件先注册，重复启动只唤出窗口。
2. `ensure_home` → 按 `run/state.json` 清理路径匹配的残留 sidecar → 读取/生成 `config.json`。
3. 若 `/healthz` 已可用（上一次留下的服务），直接打开 `/`。
4. `ls-server init-env` 生成 `.env`；用其中的 `LS_PG_ADMIN_PASSWORD` 运行 `initdb`
   （`scram-sha-256`、UTF8、locale C），写入受管 `postgresql.conf` 块并 `pg_ctl start`。
5. `ls-server bootstrap`：建主库、schema、迁移、视图、角色密码；迁移前与每日首次自动备份。
6. 启动 `ls-server serve`，写 `run/state.json`，轮询 `/healthz` 200 后跳到 `/`；
   首次进入 `/` 会 303 到 `/setup`。
7. 退出时先停 sidecar，再 `pg_ctl stop -m fast`（失败退回 immediate）。

关键约定：

- `/healthz` 的 `initialized` 表示“已有账号”，首次建号前为 false，因此桌面壳等待的是
  HTTP 200 而不是 `initialized=true`。
- `pg_ctl start` 的 stdout/stderr 必须重定向到文件或 null；postgres 会继承句柄，
  用管道会让父进程永远等不到 EOF。
- 含中文的安装目录或用户名不会被直接传给 `initdb`/`pg_ctl`：非 ASCII 的数据目录与
  `resources/pgsql` 各映射一个空闲盘符（`D:`–`Z:`），PG、sidecar 和备份都只看到盘符路径；
  Rust 侧读写配置/日志仍用原始路径。退出时释放本次使用的盘符。
  Windows CI 覆盖“中文安装目录 + ASCII 数据目录”与“ASCII 安装目录 + 中文数据目录”两次 `--smoke`。
- 关闭窗口只隐藏；托盘菜单含“打开学习空间 / 手机访问… / 粘贴导入 / 重新启动本地服务 / 打开日志目录 / 退出”。

CI（`.github/workflows/desktop.yml`，仅 `workflow_dispatch` 与 `v*` 触发）：

1. `windows-latest` 上同步依赖、安装 PyInstaller 与 Tauri CLI。
2. 缓存并构建 PG/pgvector/sidecar，`tauri build --bundles nsis`。
3. NSIS 静默安装到 `C:\ls-install`，用临时 `LS_HOME` 运行
   `learning-space-desktop.exe --smoke`，失败时打印日志；随后在含中文的安装目录
   （`C:\ls-parent\安装测试`）与中文 `LS_HOME` 下重复一次。
4. 上传安装包、体积报告与 `smoke-report.json`。

`macos-14`（arm64）job 对应 Phase 4：

1. 同步依赖、安装 PyInstaller 与 Tauri CLI，缓存 `packaging/build/pgsql` 与
   `packaging/.cache`（键含 `manifest-macos.json` 与两个 macOS 脚本的 hash）。
2. `uv run python packaging/build.py` 源码构建 PostgreSQL + pgvector + sidecar。
3. `tauri build --bundles app --target aarch64-apple-darwin`，然后对 app 内嵌套二进制
   逐个 ad-hoc 签名并 `codesign --verify --deep --strict`。
4. 在 `.app` 内用临时 `LS_HOME` 运行 `Contents/MacOS/learning-space-desktop --smoke`，
   输出 `smoke-report.json` 并打印日志。
5. `hdiutil` 生成 DMG（附 `/Applications` 快捷方式）并 `hdiutil verify`；上传 DMG、
   体积报告与 smoke 结果。

## 8. 局域网共享（Phase 3）

设计目标：手机与电脑在同一 Wi-Fi 下访问同一个实例，默认关闭、手动开启，数据库不对外。

- 新增 Tauri `share` 窗口（`desktop/ui/share.html`）：显示局域网 URL、二维码、开关与风险提示。
  二维码由 Rust `qrcode` 生成 SVG，界面不依赖外部服务；`--share` 或托盘“手机访问…”打开。
- LAN 地址来自 `sysinfo` 网卡枚举：跳过 VMware/WSL/Docker/utun 等虚拟网卡，优先
  192.168/16、10/8、172.16/12。手机必须与这些地址互通（访客网络/AP 隔离会导致失败）。
- 开启前要求 `/healthz` 已就绪且 `initialized=true`，避免手机在创建账号前抢注。
- 开关走 `respawn_server`：先停 sidecar，再以 `--host 0.0.0.0` 重启并轮询 `/healthz`，
  成功后把 `config.json` 的 `lan_enabled` 持久化；失败时自动回滚到 `127.0.0.1`。
  桌面窗口始终访问 `127.0.0.1`，不受影响。
- 关闭开关立即恢复 `127.0.0.1`；退出应用时 sidecar 与 PostgreSQL 一并停止。
- 明文 HTTP 例外沿用 Phase 1：`LS_DESKTOP_MODE=1` 时关闭 Secure Cookie 与 HSTS；
  普通生产部署仍强制 Secure/HSTS。CSRF 与登录节流在 LAN 下照常生效。
- PostgreSQL 的 `postgresql.conf` 只写 `listen_addresses = '127.0.0.1'`，
  `pg_hba.conf` 仅放行回环地址；LAN 开关不会改动数据库。
- Windows 首次监听 `0.0.0.0` 会触发防火墙授权（`ls-server.exe`），文档提示允许“专用网络”，
  应用不静默修改防火墙规则。macOS 同理。

2026-10-29 本机验证：安装包启动 `--share` → `/setup` 建号 → 开关开启后
`netstat` 显示 `0.0.0.0:8765`，通过 `http://10.47.125.30:8765/` 登录并打开仪表盘，
`Set-Cookie` 不带 Secure、无 HSTS；同时 `127.0.0.1:55432` 正常监听，
`10.47.125.30:55432` 连接超时。关闭开关后应用端口恢复 `127.0.0.1`，手机地址立即不可达，
`config.json` 的 `lan_enabled` 同步为 false。真实手机（同一 Wi-Fi）仍需人工验收。

## 9. 预算与降级路径

| 项目 | 预算 | 当前实测 | 判断 |
| --- | --- | --- | --- |
| Windows 安装包 | ≤120 MB | **35.9 MiB**（NSIS，x64，0.2.0） | 通过 |
| Windows 安装后 | ≤350 MB | 约 111 MiB（资源 101.5 MiB + 主程序 9.6 MiB） | 通过 |
| macOS DMG | ≤150 MB | CI 构建，体积由 `size-report.md` 与上传产物记录 | Phase 4 待首次 CI 结果 |
| 首次启动 | 可接受 | 安装后 `--smoke` 12–17 s（含 initdb 9–10 s）；热启动 3.5 s | 通过 |

安装包与首次启动数据来自本机 Windows 11 x64 实测（2026-10-29）。安装到含中文的目录、
数据目录也使用中文路径时，`--smoke` 同样通过；`initdb` 前会为两者建立 `subst` 盘符。

如果后续版本超预算，按顺序降级：去掉 ICU/NLS 词典与未使用 contrib → 换更小的 PostgreSQL 发行版 → 最后才考虑首次启动下载。当前不需要降级。

## 10. 风险与未决项

- **中文路径**：资源与数据目录都通过 `subst` 盘符解决，CI 覆盖两种中文路径组合；仍依赖 22 个可用盘符，并需在干净用户机上覆盖“中文用户名 + 默认安装目录”。
- **macOS 扩展构建**：改为在 macOS 上从固定 SHA-256 的源码构建 PostgreSQL 16.10 与
  pgvector 0.8.0，避免依赖不存在的 arm64 预编译包；CI 在 `macos-14` 上验证，尚未在真机人工验收。
- **签名**：MVP 不签名（macOS 仅 ad-hoc），Windows SmartScreen 和 macOS Gatekeeper 会提示；
  正式分发需证书与公证。
- **局域网 HTTP**：手机访问走明文 HTTP，仅限可信 Wi-Fi，默认关闭；PG 端口永不对外。
  手机真机（扫描二维码、导入、复习）仍需在同一 Wi-Fi 下人工验收；访客网络/AP 隔离无法访问。
- **体积报告**：`packaging/build.py` 与 desktop workflow 已输出资源与安装包体积；后续接入发布门槛。

## 11. 发布步骤（0.2.0）

MVP 不接入自动更新，按以下顺序手动发版：

1. 确认主分支 CI（`ci.yml`）与 `Desktop packaging` 的 Windows/macOS 两个 job 都通过；
   下载 artifacts：`windows-nsis` 的 `*.exe` 与 `macos-dmg` 的 `*.dmg`。
2. 版本号：`pyproject.toml`、`app/mcp_server.py`、`desktop/package.json`、
   `desktop/src-tauri/{Cargo.toml,tauri.conf.json}` 保持一致（当前 `0.2.0`）。
3. 打标签 `v0.2.0` 并推送，触发 `Desktop packaging` 重新构建（标签构建用于留档）。
4. 在 GitHub Release 上传 NSIS、DMG 与 `size-report.md`，附 SHA-256：
   `certutil -hashfile <安装包> SHA256`（Windows）或 `shasum -a 256 <dmg>`（macOS）。
5. 发布说明至少包含：Windows SmartScreen / macOS Gatekeeper 绕过方式、数据目录位置、
   LAN 共享的安全边界、从 0.1.0 升级时自动备份与迁移。

升级安装会覆盖程序资源，但保留 `LS_HOME`；首次启动检测到待执行迁移时先备份再迁移。

