"""桌面运行时入口：环境准备、数据库初始化、服务启动、备份与自检。

打包后由 Tauri 壳调用：``ls-server bootstrap`` 初始化本机数据库，
``ls-server serve`` 启动 FastAPI。也可以直接 ``python -m app.desktop``。

所有路径与密钥都落在 ``LS_HOME``，默认是平台用户数据目录；不使用仓库里的
``.env``，也不把密钥写进日志。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TypedDict
from urllib.parse import quote

DEFAULT_APP_PORT = 8765
DEFAULT_PG_PORT = 55432
DATABASE_NAME = "learning_space"

class DesktopConfig(TypedDict):
    home: Path
    env_file: Path
    app_port: int
    pg_port: int
    admin_url: str
    database_url: str
    t2s_url: str
    postgres_password: str
    app_password: str
    t2s_password: str
    backup_dir: Path
    log_dir: Path


def default_home() -> Path:
    if sys.platform == "win32":
        base = os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(base) / "com.learningspace.desktop"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "com.learningspace.desktop"
    return Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share") / (
        "com.learningspace.desktop"
    )


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    body = "\n".join(f"{key}={values[key]}" for key in sorted(values)) + "\n"
    path.write_text(body, encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def _restrict_dir(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.chmod(0o700)


def _url(user: str, password: str, port: int, database: str) -> str:
    return (
        f"postgresql://{user}:{quote(password, safe='')}"
        f"@127.0.0.1:{port}/{database}"
    )


def configure_environment(
    home: Path | None = None,
    *,
    pg_port: int | None = None,
    app_port: int | None = None,
    bind_host: str | None = None,
) -> DesktopConfig:
    """准备 LS_HOME 目录、生成缺失密钥、写入 .env，并导出进程环境。"""
    base = Path(home or os.getenv("LS_HOME") or default_home()).resolve()
    for sub in ("pgdata", "logs", "backups", "run"):
        (base / sub).mkdir(parents=True, exist_ok=True)
        _restrict_dir(base / sub)
    _restrict_dir(base)

    env_path = base / ".env"
    stored = _read_env_file(env_path)

    def pick(key: str, default: str) -> str:
        return os.environ.get(key) or stored.get(key) or default

    resolved_pg_port = int(pg_port or pick("LS_PG_PORT", str(DEFAULT_PG_PORT)))
    resolved_app_port = int(app_port or pick("LS_PORT", str(DEFAULT_APP_PORT)))
    postgres_pw = pick("LS_PG_ADMIN_PASSWORD", secrets.token_urlsafe(32))
    app_pw = pick("LS_APP_PASSWORD", secrets.token_urlsafe(32))
    t2s_pw = pick("LS_T2S_PASSWORD", secrets.token_urlsafe(32))
    session_secret = pick("SESSION_SECRET", secrets.token_urlsafe(48))

    admin_url = _url("postgres", postgres_pw, resolved_pg_port, "postgres")
    database_url = _url("ls_app", app_pw, resolved_pg_port, DATABASE_NAME)
    t2s_url = _url("t2s_reader", t2s_pw, resolved_pg_port, DATABASE_NAME)
    test_url = _url("ls_app", app_pw, resolved_pg_port, DATABASE_NAME + "_test")

    updated = {
        "ADMIN_DATABASE_URL": admin_url,
        "DATABASE_URL": database_url,
        "T2S_DATABASE_URL": t2s_url,
        "TEST_DATABASE_URL": test_url,
        "SESSION_SECRET": session_secret,
        "LS_PG_PORT": str(resolved_pg_port),
        "LS_PORT": str(resolved_app_port),
        "LS_PG_ADMIN_PASSWORD": postgres_pw,
        "LS_APP_PASSWORD": app_pw,
        "LS_T2S_PASSWORD": t2s_pw,
    }
    stored.update(updated)
    if _read_env_file(env_path) != stored:
        _write_env_file(env_path, stored)

    os.environ.update(updated)
    os.environ.update(
        {
            "APP_ENV": "production",
            "LS_HOME": str(base),
            "LS_ENV_FILE": str(env_path),
            "LS_DATA_DIR": str(base),
            "LS_DESKTOP_MODE": "1",
            "LS_ALLOW_INSECURE_HTTP": "1",
        }
    )
    if bind_host:
        os.environ["LS_BIND_HOST"] = bind_host

    return {
        "home": base,
        "env_file": env_path,
        "app_port": resolved_app_port,
        "pg_port": resolved_pg_port,
        "admin_url": admin_url,
        "database_url": database_url,
        "t2s_url": t2s_url,
        "postgres_password": postgres_pw,
        "app_password": app_pw,
        "t2s_password": t2s_pw,
        "backup_dir": base / "backups",
        "log_dir": base / "logs",
    }


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    level = os.getenv("LS_LOG_LEVEL", "info").upper()
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    handlers.append(
        RotatingFileHandler(
            log_dir / "ls-server.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
    )
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def _pg_tool(name: str, pg_bin: str | None) -> str:
    executable = name + (".exe" if os.name == "nt" else "")
    if pg_bin:
        candidate = Path(pg_bin) / executable
        if candidate.exists():
            return str(candidate)
        raise SystemExit(f"未在 {pg_bin} 找到 {executable}")
    found = shutil.which(name)
    if not found:
        raise SystemExit(
            f"未找到 {name}。请安装 PostgreSQL 客户端，或通过 --pg-bin 指定内置目录。"
        )
    return found


def _pg_env(url: str) -> dict[str, str]:
    from app.services.backup_service import connection_params

    env = dict(os.environ)
    env["PGPASSWORD"] = connection_params(url)["password"]
    return env


def run_backup(
    url: str,
    directory: Path,
    *,
    pg_bin: str | None = None,
    keep: int = 7,
) -> Path:
    from app.services.backup_service import build_pg_dump_command, dump_filename

    directory.mkdir(parents=True, exist_ok=True)
    output = directory / dump_filename()
    command = build_pg_dump_command(url, output, _pg_tool("pg_dump", pg_bin))
    try:
        subprocess.run(command, env=_pg_env(url), check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"pg_dump 执行失败，退出码 {exc.returncode}") from exc
    if keep > 0:
        dumps = sorted(
            directory.glob("learning-space-*.dump"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for stale in dumps[keep:]:
            stale.unlink()
    return output


def _admin_database_url(cfg: DesktopConfig) -> str:
    from app.services.backup_service import with_target_database

    return with_target_database(str(cfg["admin_url"]), DATABASE_NAME)


def backup_due(directory: Path) -> bool:
    dumps = list(directory.glob("learning-space-*.dump"))
    if not dumps:
        return True
    newest = max(path.stat().st_mtime for path in dumps)
    return time.strftime("%Y-%m-%d", time.localtime(newest)) != time.strftime("%Y-%m-%d")


def run_restore(
    url: str,
    dump: Path,
    *,
    pg_bin: str | None = None,
) -> None:
    from app.services.backup_service import build_pg_restore_command

    command = build_pg_restore_command(url, dump, _pg_tool("pg_restore", pg_bin))
    try:
        subprocess.run(command, env=_pg_env(url), check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"pg_restore 执行失败，退出码 {exc.returncode}") from exc


def _bootstrap(args) -> DesktopConfig:
    cfg = configure_environment(
        args.home, pg_port=args.pg_port, app_port=args.port
    )
    setup_logging(cfg["log_dir"])

    from app.bootstrap import admin_connect, initialize_database, set_role_passwords
    from app.config import settings

    with admin_connect(cfg["admin_url"]) as admin:
        set_role_passwords(admin, {"postgres": cfg["postgres_password"]})

    pg_bin = args.pg_bin or os.getenv("LS_PG_BIN")
    backup_dir = cfg["backup_dir"]
    backup_url = _admin_database_url(cfg)

    def before_migrate(pending: list[str]) -> None:
        if args.no_backup:
            return
        run_backup(backup_url, backup_dir, pg_bin=pg_bin, keep=args.keep)

    result = initialize_database(
        DATABASE_NAME,
        False,
        sql_dir=settings.base_dir / "sql",
        role_passwords={
            "ls_app": cfg["app_password"],
            "t2s_reader": cfg["t2s_password"],
        },
        before_migrate=before_migrate,
    )
    if not result["created"] and not args.no_backup and backup_due(backup_dir):
        run_backup(backup_url, backup_dir, pg_bin=pg_bin, keep=args.keep)
    return cfg


def cmd_bootstrap(args) -> int:
    cfg = _bootstrap(args)
    from app.bootstrap import DEFAULT_DATABASE

    print(
        json.dumps(
            {
                "ok": True,
                "database": DEFAULT_DATABASE,
                "home": str(cfg["home"]),
                "port": cfg["app_port"],
                "pg_port": cfg["pg_port"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def cmd_init_env(args) -> int:
    cfg = configure_environment(
        args.home, pg_port=args.pg_port, app_port=args.port
    )
    print(
        json.dumps(
            {
                "ok": True,
                "home": str(cfg["home"]),
                "env_file": str(cfg["env_file"]),
                "app_port": cfg["app_port"],
                "pg_port": cfg["pg_port"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def cmd_backup(args) -> int:
    cfg = configure_environment(args.home, pg_port=args.pg_port, app_port=args.port)
    output = run_backup(
        _admin_database_url(cfg),
        cfg["backup_dir"],
        pg_bin=args.pg_bin or os.getenv("LS_PG_BIN"),
        keep=args.keep,
    )
    print(json.dumps({"ok": True, "backup": str(output)}, ensure_ascii=False))
    return 0


def cmd_restore(args) -> int:
    from app.services.backup_service import (
        assert_restore_target,
        with_target_database,
    )

    cfg = configure_environment(args.home, pg_port=args.pg_port, app_port=args.port)
    target = args.database or DATABASE_NAME
    try:
        assert_restore_target(target, force=args.force)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    url = with_target_database(cfg["admin_url"], target)
    run_restore(url, Path(args.dump), pg_bin=args.pg_bin or os.getenv("LS_PG_BIN"))
    print(json.dumps({"ok": True, "restored": target}, ensure_ascii=False))
    return 0


def cmd_doctor(args) -> int:
    cfg = configure_environment(args.home, pg_port=args.pg_port, app_port=args.port)
    checks: dict[str, object] = {}
    checks["home_exists"] = cfg["home"].is_dir()
    checks["env_exists"] = cfg["env_file"].is_file()
    pg_bin = args.pg_bin or os.getenv("LS_PG_BIN")
    checks["pg_bin"] = pg_bin or "PATH"
    checks["pg_dump"] = shutil.which("pg_dump") is not None or bool(
        pg_bin and (Path(pg_bin) / ("pg_dump.exe" if os.name == "nt" else "pg_dump")).exists()
    )

    import psycopg

    from app.bootstrap import pending_migrations, user_count

    try:
        with psycopg.connect(cfg["database_url"], connect_timeout=5) as conn:
            checks["db"] = "up"
            checks["users"] = user_count(conn)
    except Exception as exc:
        checks["db"] = f"error: {exc.__class__.__name__}"
        checks["users"] = "unknown"
    try:
        with psycopg.connect(
            _admin_database_url(cfg), connect_timeout=5
        ) as conn:
            checks["pending_migrations"] = pending_migrations(conn)
    except Exception as exc:
        checks["pending_migrations"] = f"error: {exc.__class__.__name__}"
    try:
        with psycopg.connect(cfg["t2s_url"], connect_timeout=5) as conn:
            conn.execute("SELECT 1")
            checks["t2s_db"] = "up"
    except Exception as exc:
        checks["t2s_db"] = f"error: {exc.__class__.__name__}"
    checks["backups"] = len(list(cfg["backup_dir"].glob("*.dump")))

    for key, value in checks.items():
        print(f"[CHECK] {key}: {value}")
    return 0 if checks.get("db") == "up" else 1


def cmd_serve(args) -> int:
    cfg = configure_environment(
        args.home,
        pg_port=args.pg_port,
        app_port=args.port,
        bind_host=args.host,
    )
    setup_logging(cfg["log_dir"])
    host = args.host or os.getenv("LS_BIND_HOST") or "127.0.0.1"
    port = cfg["app_port"]

    import uvicorn

    from app.main import app

    logging.getLogger("learning_space").info(
        "starting server on %s:%s (desktop home %s)", host, port, cfg["home"]
    )
    uvicorn.run(app, host=host, port=port, log_config=None)
    return 0


def cmd_smoke(args) -> int:
    cfg = _bootstrap(args)
    port = cfg["app_port"]

    import uvicorn

    from app.main import app

    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", log_config=None
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    payload: dict | None = None
    setup_status = 0
    output: Path | None = None
    deadline = time.time() + 40
    try:
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/healthz", timeout=2
                ) as response:
                    data = json.load(response)
                if data.get("ok"):
                    payload = data
                    break
            except Exception:
                time.sleep(0.5)
        if payload is None:
            raise SystemExit("smoke 失败：/healthz 未就绪")

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/setup", timeout=5
        ) as response:
            setup_status = response.status
        if setup_status != 200:
            raise SystemExit(f"smoke 失败：/setup 返回 {setup_status}")

        output = run_backup(
            _admin_database_url(cfg),
            cfg["backup_dir"],
            pg_bin=args.pg_bin or os.getenv("LS_PG_BIN"),
            keep=args.keep,
        )
    finally:
        server.should_exit = True
        thread.join(timeout=15)

    if output is None:
        raise SystemExit("smoke 失败：未生成备份")
    print(
        json.dumps(
            {
                "ok": True,
                "health": payload,
                "setup_status": setup_status,
                "backup": str(output),
                "home": str(cfg["home"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ls-server", description="学习空间桌面运行时")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", type=Path, help="用户数据目录（默认 LS_HOME）")
    common.add_argument("--pg-port", type=int, help="内置 PostgreSQL 端口")
    common.add_argument("--port", type=int, help="应用 HTTP 端口")
    common.add_argument("--pg-bin", help="内置 PostgreSQL bin 目录")
    common.add_argument("--keep", type=int, default=7, help="保留最近 N 份备份")
    common.add_argument(
        "--no-backup", action="store_true", help="bootstrap/smoke 时跳过备份"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-env", parents=[common], help="生成 .env 与数据目录")
    sub.add_parser(
        "bootstrap", parents=[common], help="建库并应用 schema/迁移/角色"
    )
    sub.add_parser("backup", parents=[common], help="备份数据库")
    restore = sub.add_parser("restore", parents=[common], help="从 .dump 恢复")
    restore.add_argument("dump")
    restore.add_argument("--database", help="目标库名，默认 learning_space")
    restore.add_argument("--force", action="store_true", help="允许覆盖非测试库")
    sub.add_parser("doctor", parents=[common], help="检查环境与数据库状态")
    serve = sub.add_parser("serve", parents=[common], help="启动 HTTP 服务")
    serve.add_argument("--host", help="绑定地址，默认 127.0.0.1")
    sub.add_parser("smoke", parents=[common], help="初始化临时环境并自检")
    return parser


def main(argv: list[str] | None = None) -> int:
    # The Windows desktop shell pipes stdout back to Rust; the default cp1252
    # console encoding cannot represent an LS_HOME path with Chinese.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    parser = build_parser()
    args = parser.parse_args(argv)

    commands = {
        "init-env": cmd_init_env,
        "bootstrap": cmd_bootstrap,
        "backup": cmd_backup,
        "restore": cmd_restore,
        "doctor": cmd_doctor,
        "serve": cmd_serve,
        "smoke": cmd_smoke,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
