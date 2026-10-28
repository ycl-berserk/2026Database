"""建立演示数据：账号、课程、来源，并通过真实导入服务写入聊天记录。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg
from psycopg.rows import dict_row

from app.audit import set_actor
from app.auth import hash_password
from app.config import settings
from app.services import import_service

DEMO_USERS = [
    ("alice", "Alice123!", "林晓", "student"),
    ("bob", "Bob123!", "周博", "student"),
    ("admin", "Admin123!", "系统管理员", "admin"),
]

DEMO_COURSES = [
    ("DB", "数据库系统概论", "2026 秋", "陈老师", "关系模型、SQL、规范化与事务。"),
    ("OS", "操作系统", "2026 秋", "刘老师", "进程、调度、内存与文件系统。"),
    ("NET", "计算机网络", "2026 春", "赵老师", "分层协议、TCP/IP 与网络安全。"),
    ("ML", "机器学习", "2026 春", "孙老师", "监督学习、模型评估与特征工程。"),
    ("DS", "数据结构", "2025 秋", "钱老师", "线性表、树、图与经典算法。"),
]

DEMO_MEMBERS = {
    "OS": [("bob", "editor")],
    "DB": [("bob", "viewer")],
}

DEMO_SOURCES = [
    ("alice", "qoder", "alice@qoder"),
    ("alice", "chatgpt", "alice@chatgpt"),
    ("alice", "deepseek", "alice@deepseek"),
    ("bob", "chatgpt", "bob@chatgpt"),
]

SAMPLE_FILES = [
    "qoder_db.json",
    "chatgpt_os.json",
    "deepseek_net.md",
    "qoder_ml.json",
    "chatgpt_ds.json",
]

DEMO_TAG_EDGES = [
    ("SQL", "索引"),
    ("索引", "B+树"),
    ("索引", "查询执行"),
    ("事务", "隔离级别"),
    ("隔离级别", "并发"),
    ("并发", "锁"),
    ("范式", "函数依赖"),
    ("视图", "权限"),
    ("进程", "线程"),
    ("调度", "FCFS"),
    ("同步", "死锁"),
    ("虚拟内存", "分页"),
    ("分页", "页面置换"),
    ("页面置换", "LRU"),
    ("文件系统", "inode"),
    ("TCP", "握手"),
    ("TCP", "拥塞控制"),
    ("IP", "子网"),
    ("HTTPS", "TLS"),
    ("过拟合", "正则化"),
    ("评估", "交叉验证"),
    ("梯度下降", "优化"),
    ("逻辑回归", "分类"),
    ("决策树", "集成学习"),
    ("评价指标", "不平衡"),
    ("二叉树", "遍历"),
    ("二叉树", "平衡树"),
    ("平衡树", "AVL"),
    ("图", "BFS"),
    ("图", "最短路径"),
    ("最短路径", "Dijkstra"),
    ("排序", "分治"),
]


def seed_tag_edges(conn, user_id: int) -> int:
    names = {name for pair in DEMO_TAG_EDGES for name in pair}
    rows = conn.execute(
        "SELECT id, name FROM app.tags WHERE owner_id = %s AND name = ANY(%s)",
        (user_id, sorted(names)),
    ).fetchall()
    tag_ids = {row["name"]: row["id"] for row in rows}
    missing = names - set(tag_ids)
    if missing:
        raise SystemExit(f"标签缺失，无法建立先修图: {sorted(missing)}")
    for from_name, to_name in DEMO_TAG_EDGES:
        conn.execute(
            "INSERT INTO app.tag_edges (from_tag_id, to_tag_id) VALUES (%s, %s)",
            (tag_ids[from_name], tag_ids[to_name]),
        )
    return len(DEMO_TAG_EDGES)


def seed_annotations(conn, user_ids: dict[str, int]) -> tuple[int, int]:
    rows = conn.execute(
        """
        SELECT m.id AS message_id, m.content, m.seq_no,
               v.id AS conversation_id, c.code AS course_code
          FROM app.messages m
          JOIN app.conversations v ON v.id = m.conversation_id
          JOIN app.sources s ON s.id = v.source_id
          JOIN app.courses c ON c.id = v.course_id
         WHERE m.sender = 'assistant'
           AND s.owner_id = %s
         ORDER BY c.code, v.id, m.seq_no
        """,
        (user_ids["alice"],),
    ).fetchall()

    by_conversation: dict[int, list[dict]] = {}
    for row in rows:
        by_conversation.setdefault(row["conversation_id"], []).append(row)

    note_lines = [
        "把这里的结论抄进笔记：先写定义，再写适用条件，最后补一个反例。",
        "这一段是本章主线，复习时先合上书复述，再回来核对细节。",
        "注意公式里每个符号的含义，考试经常在符号上设陷阱。",
        "这个例子很典型，可以当作同类题目的模板。",
    ]
    question_lines = [
        "如果去掉这里的前提条件，结论还成立吗？需要找一个反例验证。",
        "这两步之间的推导没看懂，下次复习要重点回看。",
        "老师课上提过类似问题，和这里的区别在哪里？",
        "这里的复杂度分析是否忽略了常数项？想确认一下边界情况。",
    ]
    correction_lines = [
        "这里容易把相邻概念混淆，复习时用表格对比两者的定义与场景。",
        "术语要和教材保持一致，作业里不要使用口语化说法。",
        "这个结论只适用于特定条件，别直接套到所有情况。",
        "之前一直理解反了，已按这段解释改正，并记下判断依据。",
    ]
    kinds = ["note", "question", "correction", "summary"]

    annotation_ids: list[int] = []
    index = 0
    for conversation_id in sorted(by_conversation):
        messages = by_conversation[conversation_id]
        picked = [messages[0], messages[3]] if len(messages) >= 4 else messages[:2]
        for message in picked:
            kind = kinds[index % len(kinds)]
            if kind == "note":
                content = note_lines[index % len(note_lines)]
            elif kind == "question":
                content = question_lines[index % len(question_lines)]
            elif kind == "correction":
                content = correction_lines[index % len(correction_lines)]
            else:
                flat = " ".join(message["content"].split())
                content = f"一句话总结：{flat[:70]}…"
            status = "resolved" if index % 3 == 0 else "open"
            row = conn.execute(
                """
                INSERT INTO app.annotations (message_id, user_id, kind, content, status)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    message["message_id"],
                    user_ids["alice"],
                    kind,
                    f"[{message['course_code']}] {content}",
                    status,
                ),
            ).fetchone()
            annotation_ids.append(row["id"])
            index += 1

    os_messages = [
        row
        for row in rows
        if row["course_code"] == "OS"
    ]
    bob_ids: list[int] = []
    for position, message in enumerate(os_messages[1:7]):
        row = conn.execute(
            """
            INSERT INTO app.annotations (message_id, user_id, kind, content, status)
            VALUES (%s, %s, %s, %s, 'open')
            RETURNING id
            """,
            (
                message["message_id"],
                user_ids["bob"],
                "question" if position % 2 == 0 else "note",
                f"共享课程批注：{question_lines[position % len(question_lines)]}",
            ),
        ).fetchone()
        bob_ids.append(row["id"])

    alice_review = annotation_ids[:35]
    for position, annotation_id in enumerate(alice_review):
        if position % 5 == 0:
            status = "mastered"
        elif position % 3 == 0:
            status = "reviewing"
        else:
            status = "pending"
        interval = [1, 2, 4, 8][position % 4]
        due_offset = [(-2), (-1), 0, 1, 3][position % 5]
        conn.execute(
            """
            INSERT INTO app.review_items
                (user_id, annotation_id, status, due_date, interval_days,
                 review_count, last_reviewed_at)
            VALUES (%s, %s, %s, current_date + %s, %s, %s,
                    CASE WHEN %s > 0 THEN now() - make_interval(days => %s) ELSE NULL END)
            """,
            (
                user_ids["alice"],
                annotation_id,
                status,
                due_offset,
                interval,
                position % 5,
                position % 5,
                position % 5,
            ),
        )

    for position, annotation_id in enumerate(bob_ids[:4]):
        conn.execute(
            """
            INSERT INTO app.review_items
                (user_id, annotation_id, status, due_date, interval_days, review_count)
            VALUES (%s, %s, %s, current_date + %s, %s, %s)
            """,
            (
                user_ids["bob"],
                annotation_id,
                "reviewing" if position % 2 else "pending",
                position % 3,
                position + 1,
                position,
            ),
        )

    # 为已有复习次数补历史事件，让热力图在演示库里直接可见。
    conn.execute(
        """
        INSERT INTO app.review_logs
            (user_id, review_item_id, action, interval_days, due_date,
             review_count, created_at)
        SELECT ri.user_id, ri.id, 'review', 1, ri.due_date, gs,
               now() - make_interval(days => ((ri.review_count - gs) * 3 + (ri.id % 4))::int)
          FROM app.review_items ri
          CROSS JOIN LATERAL generate_series(1, ri.review_count) AS gs
         WHERE ri.review_count > 0
        """
    )

    return len(annotation_ids) + len(bob_ids), len(alice_review) + min(4, len(bob_ids))


def main() -> int:
    parser = argparse.ArgumentParser(description="写入演示数据")
    parser.add_argument(
        "--if-empty",
        action="store_true",
        help="已有用户时安静退出 0，供容器启动脚本重复调用",
    )
    args = parser.parse_args()

    with psycopg.connect(settings.database_url, autocommit=False, row_factory=dict_row) as conn:
        existing = conn.execute("SELECT count(*) AS n FROM app.users").fetchone()["n"]
        if existing:
            if args.if_empty:
                print(f"users already exist ({existing}); skip seeding")
                return 0
            print(f"users already exist ({existing}); aborting to avoid duplicates")
            return 1

        user_ids: dict[str, int] = {}
        for username, password, display_name, role in DEMO_USERS:
            row = conn.execute(
                "INSERT INTO app.users (username, password_hash, display_name, system_role) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (username, hash_password(password), display_name, role),
            ).fetchone()
            user_ids[username] = row["id"]

        set_actor(conn, user_ids["alice"])
        course_ids: dict[str, int] = {}
        for code, name, semester, instructor, description in DEMO_COURSES:
            row = conn.execute(
                "INSERT INTO app.courses (code, name, semester, instructor, description, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (code, name, semester, instructor, description, user_ids["alice"]),
            ).fetchone()
            course_ids[code] = row["id"]
            conn.execute(
                "INSERT INTO app.course_members (course_id, user_id, role) VALUES (%s, %s, 'owner')",
                (row["id"], user_ids["alice"]),
            )

        for code, members in DEMO_MEMBERS.items():
            for username, role in members:
                conn.execute(
                    "INSERT INTO app.course_members (course_id, user_id, role) VALUES (%s, %s, %s)",
                    (course_ids[code], user_ids[username], role),
                )

        for username, platform, label in DEMO_SOURCES:
            conn.execute(
                "INSERT INTO app.sources (owner_id, platform, account_label) VALUES (%s, %s, %s)",
                (user_ids[username], platform, label),
            )

        courses = import_service.editable_courses(conn, user_ids["alice"])
        course_by_code = {c["code"]: c["id"] for c in courses}
        sample_dir = ROOT / "data" / "samples"
        for file_name in SAMPLE_FILES:
            raw = (sample_dir / file_name).read_bytes()
            result = import_service.parse_upload(
                file_name, raw, platform=None, account_label=None
            )
            if result.errors:
                raise SystemExit(f"{file_name} 解析失败: {result.errors[:3]}")
            with conn.transaction():
                source_id = import_service.get_or_create_source(
                    conn, user_ids["alice"], result.platform, result.account_label
                )
                batch = import_service.save_preview(
                    conn,
                    user_id=user_ids["alice"],
                    source_id=source_id,
                    file_name=file_name,
                    raw=raw,
                    result=result,
                )
            course_map = {
                index: course_by_code.get(conv.get("course_code") or "")
                for index, conv in enumerate(result.conversations)
            }
            counts = import_service.confirm_import(
                conn,
                token=str(batch["preview_token"]),
                user={"id": user_ids["alice"], "system_role": "student"},
                selected=set(range(len(result.conversations))),
                course_map=course_map,
            )
            print(
                f"imported {file_name}: {counts['conversations_new']} conversations, "
                f"{counts['messages_new']} messages, {counts['tags_new']} tags"
            )

        annotation_count, review_count = seed_annotations(conn, user_ids)
        print(f"seeded annotations: {annotation_count}, review items: {review_count}")

        edge_count = seed_tag_edges(conn, user_ids["alice"])
        print(f"seeded tag prerequisites: {edge_count}")

        unclassified = conn.execute(
            """
            WITH picked AS (
                SELECT v.id
                  FROM app.conversations v
                  JOIN app.courses c ON c.id = v.course_id
                 WHERE c.code = 'DS'
                 ORDER BY v.id
                 LIMIT 3
            )
            UPDATE app.conversations v
               SET course_id = NULL
              FROM picked
             WHERE v.id = picked.id
            RETURNING v.id
            """
        ).fetchall()
        print(f"left {len(unclassified)} conversations in inbox")

        conn.commit()

    print("seeded users:", ", ".join(user_ids))
    print("seeded courses:", ", ".join(course_ids))
    print("demo passwords: alice/Alice123!  bob/Bob123!  admin/Admin123!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
