# 桌面打包与个人模式

> 状态：Phase 0 可行性验证完成（Windows x64）。macOS 在 Phase 4 验证。
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

把同一份二进制放到 `C:\ls-pgtest\pgsql`（ASCII）后 `initdb` 成功；用 ASCII junction 指向原目录也能成功。因此：

- 构建机、CI 和打包临时目录必须使用 ASCII 路径。
- 桌面运行时不能假定安装目录/用户名是 ASCII；Phase 2 启动 PG 时通过 ASCII junction、`subst` 盘符或 `C:\Users\Public\LearningSpace` 这类 ASCII 路径转发，禁止直接把含中文的资源路径传给 `initdb`/`pg_ctl`。
- 安装包 `productName` 保持英文 `Learning Space`，但这不是充分条件，仍需运行时处理。

## 4. 构建步骤（当前脚本）

```powershell
# 1) 下载、校验、解压、裁剪 PG（清单固定版本与 SHA-256）
.venv\Scripts\python.exe packaging/postgres/fetch_trim.py --stage all

# 2) 用 EDB 头文件构建 pgvector，并安装到裁剪树
powershell -ExecutionPolicy Bypass -File packaging/postgres/build_pgvector.ps1 `
  -PgRoot packaging/.cache/raw-16.10/pgsql `
  -InstallDir packaging/build/pgsql

# 3) 构建 Python sidecar
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean `
  --distpath packaging/build/sidecar `
  --workpath packaging/.cache/pyinstaller `
  packaging/sidecar/ls-server.spec
```

构建工具版本固定在 `packaging/requirements.txt`：PyInstaller 6.22.3、pefile 2024.8.26。`fetch_trim.py` 会删除 `include/doc/pgAdmin/StackBuilder`，排除静态 `.lib`，再按 PE 导入闭包删除未使用的 DLL（pgAdmin 的 wxWidgets、libcurl 等），只保留服务端、客户端工具及其依赖。

## 5. Sidecar 打包要点

- 使用 onedir，不使用 onefile/UPX：启动更快、杀软误报更少，且保留 `_internal`。
- `ls-server.spec` 打包 `app/templates`、`app/static`、`sql` 到 `_internal` 下对应位置，保证 `settings.base_dir` 的源路径与冻结路径一致。
- 排除 pytest、hypothesis、numpy、pandas、PIL、pptx、setuptools 等非运行时依赖；PIL 曾因开发环境被误收集，占用 13 MiB。
- `uvicorn`、`psycopg[binary]`、`sqlglot` 已通过实际启动与 `/healthz`、`/login`、静态资源请求验证。
- 冻结包启动命令由 `packaging/sidecar/entry.py` 负责；正式 Phase 1 会替换为 `ls-server serve|bootstrap|...` 子命令。

## 6. 预算与降级路径

| 项目 | 预算 | Phase 0 实测 | 判断 |
| --- | --- | --- | --- |
| Windows 安装包 | ≤120 MB | 资源约 101.4 MiB | 通过（待 NSIS 实测） |
| Windows 安装后 | ≤350 MB | 约 101.4 MiB | 通过 |
| macOS DMG | ≤150 MB | 未验证 | Phase 4 |
| 首次启动 | 可接受 | <10 s（本机） | 通过 |

如果后续版本超预算，按顺序降级：去掉 ICU/NLS 词典与未使用 contrib → 换更小的 PostgreSQL 发行版 → 最后才考虑首次启动下载。当前不需要降级。

## 7. 风险与未决项

- **中文路径**：见第 3 节，必须在 Rust 启动器中解决，不能只靠文档。Phase 2 需补一项自动化 smoke：在含中文的临时目录启动安装包，验证 `initdb` 不被路径击穿。
- **macOS 扩展构建**：需要匹配 arm64 的 PostgreSQL 头文件与 pgvector；Phase 4 在 `macos-14` 上验证。
- **签名**：MVP 不签名，Windows SmartScreen 和 macOS Gatekeeper 会提示；正式分发需证书与公证。
- **局域网 HTTP**：手机访问走明文 HTTP，仅限可信 Wi-Fi，默认关闭；PG 端口永不对外。
- **体积报告**：Phase 2 的 CI 应输出归档/安装后体积，作为发布门禁。
