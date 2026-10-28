"""用户全量导出（zip）与 pg_dump / pg_restore 命令构造。"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

from . import export_service

NOTICE = "本压缩包由「学习空间」导出；演示数据不含真实个人信息。"


def _safe_component(value: str, fallback: str) -> str:
    cleaned = "".join(ch for ch in value if ch not in '\\/:*?"<>|\r\n\t').strip()
    return cleaned[:60] or fallback


def _csv_bytes(header: list[str], rows: list[list]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if value is None else str(value) for value in row])
    return buffer.getvalue().encode("utf-8-sig")


def export_user_archive(conn, user: dict) -> tuple[str, bytes]:
    """导出用户本人来源下的会话、批注与复习项；共享课程中他人的内容不导出。"""
    conversations = conn.execute(
        """
        SELECT v.id, v.title
          FROM app.conversations v
          JOIN app.sources s ON s.id = v.source_id
         WHERE s.owner_id = %s
         ORDER BY v.id
        """,
        (user["id"],),
    ).fetchall()

    annotations = conn.execute(
        """
        SELECT v.id AS conversation_id, v.title, m.seq_no, a.kind, a.status,
               a.content, a.created_at, a.updated_at
          FROM app.annotations a
          JOIN app.messages m ON m.id = a.message_id
          JOIN app.conversations v ON v.id = m.conversation_id
          JOIN app.sources s ON s.id = v.source_id
         WHERE a.user_id = %s AND s.owner_id = %s
         ORDER BY a.id
        """,
        (user["id"], user["id"]),
    ).fetchall()

    reviews = conn.execute(
        """
        SELECT r.id, r.status, r.due_date, r.interval_days, r.review_count,
               r.last_reviewed_at, v.id AS conversation_id, v.title,
               m.seq_no, a.content AS annotation
          FROM app.review_items r
          JOIN app.annotations a ON a.id = r.annotation_id
          JOIN app.messages m ON m.id = a.message_id
          JOIN app.conversations v ON v.id = m.conversation_id
          JOIN app.sources s ON s.id = v.source_id
         WHERE r.user_id = %s AND s.owner_id = %s
         ORDER BY r.due_date, r.id
        """,
        (user["id"], user["id"]),
    ).fetchall()

    manifest = {
        "app": "学习空间",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "user": {
            "id": user["id"],
            "username": user.get("username"),
            "display_name": user.get("display_name"),
        },
        "counts": {
            "conversations": len(conversations),
            "annotations": len(annotations),
            "reviews": len(reviews),
        },
        "scope": "包含本人采集来源下的会话 Markdown、本人批注与复习项；共享课程中他人的会话不导出。",
        "notice": NOTICE,
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        archive.writestr("README.txt", NOTICE + "\n\n" + manifest["scope"] + "\n")

        used: set[str] = set()
        for row in conversations:
            filename, body = export_service.conversation_markdown(
                conn, row["id"], user
            )
            stem = f"conversations/{row['id']:04d}-{_safe_component(row['title'], 'conversation')}"
            name = stem + ".md"
            counter = 2
            while name in used:
                name = f"{stem}-{counter}.md"
                counter += 1
            used.add(name)
            archive.writestr(name, body)

        archive.writestr(
            "annotations.csv",
            _csv_bytes(
                [
                    "conversation_id",
                    "conversation_title",
                    "message_seq",
                    "kind",
                    "status",
                    "content",
                    "created_at",
                    "updated_at",
                ],
                [
                    [
                        row["conversation_id"],
                        row["title"],
                        row["seq_no"],
                        row["kind"],
                        row["status"],
                        row["content"],
                        row["created_at"],
                        row["updated_at"],
                    ]
                    for row in annotations
                ],
            ),
        )
        archive.writestr(
            "reviews.csv",
            _csv_bytes(
                [
                    "id",
                    "status",
                    "due_date",
                    "interval_days",
                    "review_count",
                    "last_reviewed_at",
                    "conversation_id",
                    "conversation_title",
                    "message_seq",
                    "annotation",
                ],
                [
                    [
                        row["id"],
                        row["status"],
                        row["due_date"],
                        row["interval_days"],
                        row["review_count"],
                        row["last_reviewed_at"],
                        row["conversation_id"],
                        row["title"],
                        row["seq_no"],
                        row["annotation"],
                    ]
                    for row in reviews
                ],
            ),
        )

    username = _safe_component(str(user.get("username") or user["id"]), "user")
    filename = f"learning-space-all-{username}-{datetime.now(UTC):%Y%m%d}.zip"
    return filename, buffer.getvalue()


def connection_params(url: str) -> dict[str, str]:
    parts = urlsplit(url)
    if parts.scheme not in ("postgresql", "postgres"):
        raise ValueError("仅支持 postgresql:// 连接串")
    database = unquote(parts.path.lstrip("/"))
    if not parts.hostname or not database:
        raise ValueError("连接串必须包含主机与数据库名")
    return {
        "host": parts.hostname,
        "port": str(parts.port or 5432),
        "user": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
        "dbname": database,
    }


def with_target_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path="/" + database.lstrip("/")))


def dump_filename(now: datetime | None = None) -> str:
    moment = now or datetime.now(UTC)
    return moment.strftime("learning-space-%Y%m%d-%H%M%S.dump")


def build_pg_dump_command(
    url: str, output: str | Path, executable: str = "pg_dump"
) -> list[str]:
    params = connection_params(url)
    return [
        executable,
        "--format=custom",
        "--no-password",
        "-h",
        params["host"],
        "-p",
        params["port"],
        "-U",
        params["user"],
        "-d",
        params["dbname"],
        "--file",
        str(output),
    ]


def build_pg_restore_command(
    url: str, source: str | Path, executable: str = "pg_restore"
) -> list[str]:
    params = connection_params(url)
    return [
        executable,
        "--clean",
        "--if-exists",
        "--no-password",
        "-h",
        params["host"],
        "-p",
        params["port"],
        "-U",
        params["user"],
        "-d",
        params["dbname"],
        str(source),
    ]


def assert_restore_target(database: str, *, force: bool) -> None:
    if database.endswith("_test") or force:
        return
    raise ValueError(
        "恢复会先清理目标库对象；默认只允许 *_test 数据库，"
        "确需覆盖其他库请显式加 --force"
    )
