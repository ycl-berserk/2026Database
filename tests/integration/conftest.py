from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.auth import hash_password
from app.config import settings


@pytest.fixture(scope="session")
def db_url() -> str:
    name = settings.test_database_url.rsplit("/", 1)[-1]
    assert name.endswith("_test"), "集成测试只能连接以 _test 结尾的数据库"
    # 测试脚手架需要 TRUNCATE 等权限，应用角色 ls_app 刻意不授予；仅测试连接使用管理员角色。
    url = settings.admin_database_url.rsplit("/", 1)[0] + "/" + name
    try:
        with psycopg.connect(url, autocommit=True) as conn:
            exists = conn.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'app' AND table_name = 'users'"
            ).fetchone()
            if exists is None:
                pytest.skip("测试库未初始化，请先运行 scripts/init_db.py --reset")
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL 测试库不可用")
    return url


@pytest.fixture
def conn(db_url):
    with psycopg.connect(db_url, autocommit=True, row_factory=dict_row) as connection:
        connection.execute("TRUNCATE app.users RESTART IDENTITY CASCADE")
        yield connection


@pytest.fixture
def t2s_conn(db_url):
    from app.config import settings as cfg

    url = cfg.t2s_database_url.rsplit("/", 1)[0] + "/learning_space_test"
    with psycopg.connect(url, autocommit=False, row_factory=dict_row) as connection:
        yield connection


@pytest.fixture
def base(conn):
    users = {}
    for username, display_name, role in (
        ("alice", "林晓", "student"),
        ("bob", "周博", "student"),
        ("admin", "管理员", "admin"),
    ):
        row = conn.execute(
            "INSERT INTO app.users (username, password_hash, display_name, system_role) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (username, hash_password("Passw0rd!"), display_name, role),
        ).fetchone()
        users[username] = row["id"]

    courses = {}
    for code, name, creator in (("DB", "数据库系统概论", "alice"), ("OS", "操作系统", "alice")):
        # 课程必须与 owner 成员在同一事务中提交，延迟约束触发器才会通过。
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO app.courses (code, name, semester, created_by) "
                "VALUES (%s, %s, '2026 秋', %s) RETURNING id",
                (code, name, users[creator]),
            ).fetchone()
            conn.execute(
                "INSERT INTO app.course_members (course_id, user_id, role) "
                "VALUES (%s, %s, 'owner')",
                (row["id"], users[creator]),
            )
        courses[code] = row["id"]
    conn.execute(
        "INSERT INTO app.course_members (course_id, user_id, role) VALUES (%s, %s, 'viewer')",
        (courses["DB"], users["bob"]),
    )
    conn.execute(
        "INSERT INTO app.course_members (course_id, user_id, role) VALUES (%s, %s, 'editor')",
        (courses["OS"], users["bob"]),
    )

    source = conn.execute(
        "INSERT INTO app.sources (owner_id, platform, account_label) "
        "VALUES (%s, 'qoder', 'alice@qoder') RETURNING id",
        (users["alice"],),
    ).fetchone()

    conversations = {}
    for external_id, title, course in (
        ("db-1", "事务与隔离级别", "DB"),
        ("os-1", "进程调度", "OS"),
        ("misc-1", "未归类的问题", None),
    ):
        row = conn.execute(
            "INSERT INTO app.conversations "
            "(source_id, course_id, external_id, title, started_at) "
            "VALUES (%s, %s, %s, %s, '2026-02-01T10:00:00+08:00') RETURNING id",
            (source["id"], courses.get(course), external_id, title),
        ).fetchone()
        conversations[external_id] = row["id"]

    messages = {}
    contents = [
        ("user", "事务的隔离级别有哪些？"),
        ("assistant", "SQL 标准定义了读未提交、读已提交、可重复读和串行化四种隔离级别。"),
        ("user", "可重复读能避免幻读吗？"),
        ("assistant", "在 PostgreSQL 的可重复读下，幻读也不会出现，但实现依赖快照隔离。"),
    ]
    for seq, (sender, content) in enumerate(contents, start=1):
        row = conn.execute(
            "INSERT INTO app.messages (conversation_id, seq_no, sender, content, created_at) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (
                conversations["db-1"],
                seq,
                sender,
                content,
                f"2026-02-01T10:{seq:02d}:00+08:00",
            ),
        ).fetchone()
        messages[f"db-{seq}"] = row["id"]
    for external_id, text in (("os-1", "进程调度算法有哪些？"), ("misc-1", "索引为什么能加速查询？")):
        row = conn.execute(
            "INSERT INTO app.messages (conversation_id, seq_no, sender, content) "
            "VALUES (%s, 1, 'user', %s) RETURNING id",
            (conversations[external_id], text),
        ).fetchone()
        messages[external_id] = row["id"]

    tag = conn.execute(
        "INSERT INTO app.tags (owner_id, name, kind) VALUES (%s, '事务', 'knowledge_point') "
        "RETURNING id",
        (users["alice"],),
    ).fetchone()
    conn.execute(
        "INSERT INTO app.conversation_tags (conversation_id, tag_id, created_by) "
        "VALUES (%s, %s, %s)",
        (conversations["db-1"], tag["id"], users["alice"]),
    )

    annotation = conn.execute(
        "INSERT INTO app.annotations (message_id, user_id, kind, content) "
        "VALUES (%s, %s, 'note', '隔离级别要能对比记忆。') RETURNING id",
        (messages["db-2"], users["alice"]),
    ).fetchone()
    conn.execute(
        "INSERT INTO app.review_items (user_id, annotation_id, due_date, interval_days) "
        "VALUES (%s, %s, current_date, 1)",
        (users["alice"], annotation["id"]),
    )

    return {
        "users": users,
        "courses": courses,
        "conversations": conversations,
        "messages": messages,
        "tag": tag["id"],
        "annotation": annotation["id"],
    }
