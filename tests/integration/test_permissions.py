from __future__ import annotations

import pytest

from app.errors import PermissionDenied
from app.permissions import (
    can_access_conversation,
    get_course_role,
    require_conversation_edit,
    require_course_role,
    require_system_admin,
)
from app.services import conversation_service


def _user(user_id: int, role: str = "student") -> dict:
    return {"id": user_id, "system_role": role}


def test_course_roles(conn, base):
    alice = _user(base["users"]["alice"])
    bob = _user(base["users"]["bob"])
    assert get_course_role(conn, base["courses"]["DB"], alice["id"]) == "owner"
    assert get_course_role(conn, base["courses"]["DB"], bob["id"]) == "viewer"
    assert get_course_role(conn, base["courses"]["OS"], bob["id"]) == "editor"
    assert get_course_role(conn, base["courses"]["OS"], 9999) is None


def test_require_course_role_blocks_viewer(conn, base):
    bob = _user(base["users"]["bob"])
    assert require_course_role(conn, base["courses"]["DB"], bob, "viewer") == "viewer"
    with pytest.raises(PermissionDenied):
        require_course_role(conn, base["courses"]["DB"], bob, "editor")
    with pytest.raises(PermissionDenied):
        require_course_role(conn, base["courses"]["DB"], _user(9999), "viewer")


def test_conversation_access_scope(conn, base):
    alice = base["users"]["alice"]
    bob = base["users"]["bob"]
    assert can_access_conversation(conn, base["conversations"]["db-1"], alice)
    assert can_access_conversation(conn, base["conversations"]["db-1"], bob)
    assert can_access_conversation(conn, base["conversations"]["os-1"], bob)
    assert not can_access_conversation(conn, base["conversations"]["misc-1"], bob)


def test_conversation_edit_scope(conn, base):
    alice = _user(base["users"]["alice"])
    bob = _user(base["users"]["bob"])
    require_conversation_edit(conn, base["conversations"]["misc-1"], alice)
    require_conversation_edit(conn, base["conversations"]["os-1"], bob)
    with pytest.raises(PermissionDenied):
        require_conversation_edit(conn, base["conversations"]["db-1"], bob)


def test_organize_requires_edit_permission(conn, base):
    bob = _user(base["users"]["bob"])
    with pytest.raises(PermissionDenied):
        conversation_service.organize_conversation(
            conn,
            base["conversations"]["db-1"],
            bob,
            course_id=str(base["courses"]["DB"]),
            tag_ids=[],
        )


def test_organize_viewer_cannot_attach_tag(conn, base):
    bob = _user(base["users"]["bob"])
    tag = conn.execute(
        "INSERT INTO app.tags (owner_id, name) VALUES (%s, 'bob-note') RETURNING id",
        (bob["id"],),
    ).fetchone()
    # bob 是 OS 编辑者，可以整理 OS 会话
    conversation_service.organize_conversation(
        conn,
        base["conversations"]["os-1"],
        bob,
        course_id=str(base["courses"]["OS"]),
        tag_ids=[tag["id"]],
    )
    linked = conn.execute(
        "SELECT count(*) AS n FROM app.conversation_tags "
        "WHERE conversation_id = %s AND tag_id = %s",
        (base["conversations"]["os-1"], tag["id"]),
    ).fetchone()["n"]
    assert linked == 1


def test_system_admin(conn, base):
    require_system_admin(_user(base["users"]["admin"], "admin"))
    with pytest.raises(PermissionDenied):
        require_system_admin(_user(base["users"]["alice"], "student"))
