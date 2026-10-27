from __future__ import annotations

from .conversation_service import get_conversation

SENDER_TITLES = {"user": "我", "assistant": "助手", "system": "系统"}
KIND_TITLES = {
    "note": "笔记",
    "question": "疑问",
    "correction": "纠错",
    "summary": "总结",
}
STATUS_TITLES = {"open": "待解决", "resolved": "已解决"}


def _safe_name(value: str, fallback: str) -> str:
    cleaned = "".join(ch for ch in value if ch not in '\\/:*?"<>|\r\n\t').strip()
    return cleaned[:60] or fallback


def conversation_markdown(conn, conversation_id: int, user: dict) -> tuple[str, str]:
    conversation = get_conversation(conn, conversation_id, user)

    messages = conn.execute(
        """
        SELECT m.id, m.seq_no, m.sender, m.content, m.created_at
          FROM app.messages m
         WHERE m.conversation_id = %s
         ORDER BY m.seq_no
        """,
        (conversation_id,),
    ).fetchall()

    annotations: dict[int, list[dict]] = {}
    if messages:
        for row in conn.execute(
            """
            SELECT a.message_id, a.kind, a.content, a.status, u.display_name
              FROM app.annotations a
              JOIN app.users u ON u.id = a.user_id
             WHERE a.message_id = ANY(%s)
             ORDER BY a.created_at
            """,
            ([row["id"] for row in messages],),
        ).fetchall():
            annotations.setdefault(row["message_id"], []).append(row)

    tags = conn.execute(
        """
        SELECT t.name
          FROM app.conversation_tags ct
          JOIN app.tags t ON t.id = ct.tag_id
         WHERE ct.conversation_id = %s
         ORDER BY t.name
        """,
        (conversation_id,),
    ).fetchall()

    lines = [
        f"# {conversation['title']}",
        "",
        f"- 课程：{conversation['course_code'] + ' ' + conversation['course_name'] if conversation['course_code'] else '未归类'}",
        f"- 平台：{conversation['platform']}",
        f"- 账号：{conversation['account_label']}",
        f"- 标签：{('、'.join(tag['name'] for tag in tags)) or '无'}",
        f"- 消息数：{len(messages)}",
        f"- 开始时间：{conversation['started_at'] or '—'}",
        f"- 结束时间：{conversation['ended_at'] or '—'}",
        "",
        "> 本文件由「学习空间」导出；演示数据不含真实个人信息。",
        "",
        "---",
        "",
    ]

    for message in messages:
        title = SENDER_TITLES.get(message["sender"], message["sender"])
        lines.append(f"## {title} · #{message['seq_no']}")
        lines.append("")
        lines.append(message["content"])
        lines.append("")
        for note in annotations.get(message["id"], []):
            kind = KIND_TITLES.get(note["kind"], note["kind"])
            status = STATUS_TITLES.get(note["status"], note["status"])
            lines.append(
                f"> **批注·{kind}**（{status}，{note['display_name']}）：{note['content']}"
            )
            lines.append("")

    filename = _safe_name(conversation["title"], f"conversation-{conversation_id}") + ".md"
    return filename, "\n".join(lines).rstrip() + "\n"
