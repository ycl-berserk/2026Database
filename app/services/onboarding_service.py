from __future__ import annotations

from typing import Any

STEPS: tuple[dict[str, str], ...] = (
    {
        "key": "course",
        "title": "创建课程",
        "description": "课程是会话归档、共享和复习的组织单位。",
        "href": "/courses/new",
    },
    {
        "key": "import",
        "title": "导入聊天记录",
        "description": "支持 JSON / Markdown、直接粘贴和浏览器快速收录。",
        "href": "/imports",
    },
    {
        "key": "annotate",
        "title": "添加第一条批注",
        "description": "在会话消息下记录笔记、疑问、纠错或总结。",
        "href": "/conversations",
    },
    {
        "key": "review",
        "title": "加入复习队列",
        "description": "把批注变成今天就能复习的卡片。",
        "href": "/annotations",
    },
)


def progress(conn, user_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            EXISTS (
                SELECT 1 FROM app.course_members
                 WHERE user_id = %(uid)s AND role = 'owner'
            ) AS has_course,
            EXISTS (
                SELECT 1
                  FROM app.conversations c
                  JOIN app.sources s ON s.id = c.source_id
                 WHERE s.owner_id = %(uid)s
            ) AS has_import,
            EXISTS (
                SELECT 1 FROM app.annotations WHERE user_id = %(uid)s
            ) AS has_annotate,
            EXISTS (
                SELECT 1 FROM app.review_items WHERE user_id = %(uid)s
            ) AS has_review,
            (
                SELECT status FROM app.user_onboarding WHERE user_id = %(uid)s
            ) AS stored_status
        """,
        {"uid": user_id},
    ).fetchone()

    done = {
        "course": bool(row["has_course"]),
        "import": bool(row["has_import"]),
        "annotate": bool(row["has_annotate"]),
        "review": bool(row["has_review"]),
    }
    steps = [
        {**step, "done": done[step["key"]]}
        for step in STEPS
    ]
    completed_count = sum(done.values())
    stored_status = row["stored_status"]
    if completed_count == len(STEPS) or stored_status == "completed":
        status = "completed"
    elif stored_status == "skipped":
        status = "skipped"
    else:
        status = "pending"

    return {
        "steps": steps,
        "completed_count": completed_count,
        "total": len(STEPS),
        "status": status,
        "stored_status": stored_status,
    }


def ensure_started(conn, user_id: int) -> bool:
    """首次零进度用户插入 pending，返回是否应跳转到引导页。"""
    state = progress(conn, user_id)
    if state["completed_count"] > 0 or state["stored_status"] is not None:
        return False
    cursor = conn.execute(
        """
        INSERT INTO app.user_onboarding (user_id, status)
        VALUES (%s, 'pending')
        ON CONFLICT (user_id) DO NOTHING
        RETURNING user_id
        """,
        (user_id,),
    )
    return cursor.fetchone() is not None


def mark_skipped(conn, user_id: int) -> None:
    _upsert_status(conn, user_id, "skipped")


def restart(conn, user_id: int) -> None:
    _upsert_status(conn, user_id, "pending")


def _upsert_status(conn, user_id: int, status: str) -> None:
    conn.execute(
        """
        INSERT INTO app.user_onboarding (user_id, status)
        VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE
           SET status = EXCLUDED.status,
               updated_at = now()
        """,
        (user_id, status),
    )
