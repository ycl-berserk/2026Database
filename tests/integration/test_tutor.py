from __future__ import annotations

import asyncio
import re

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import get_conn
from app.errors import ConflictError, NotFoundError, ValidationError
from app.main import app
from app.services import tutor_service


@pytest.fixture
def tutor_off():
    original = settings.tutor_mode
    object.__setattr__(settings, "tutor_mode", "off")
    yield
    object.__setattr__(settings, "tutor_mode", original)


def _student(base, name: str) -> dict:
    return {"id": base["users"][name], "system_role": "student"}


@pytest.fixture
def client(conn, base):
    app.dependency_overrides[get_conn] = lambda: conn
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client: TestClient) -> None:
    html = client.get("/login").text
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert match, "登录页缺少 CSRF 令牌"
    response = client.post(
        "/login",
        data={
            "username": "alice",
            "password": "Passw0rd!",
            "csrf_token": match.group(1),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_tutor_page_renders_and_creates_theory_session(client, base, tutor_off):
    _login(client)
    page = client.get("/tutor")
    assert page.status_code == 200
    assert "AI 学习" in page.text

    match = re.search(r'name="csrf-token" content="([^"]+)"', page.text)
    assert match
    response = client.post(
        "/tutor/session",
        data={
            "csrf_token": match.group(1),
            "course_id": base["courses"]["DB"],
            "mode": "theory",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    detail = client.get(response.headers["location"])
    assert detail.status_code == 200
    assert "理论答疑" in detail.text

    quiz = client.post(
        "/tutor/session",
        data={
            "csrf_token": match.group(1),
            "course_id": base["courses"]["DB"],
            "mode": "quiz",
            "tag_id": base["tag"],
        },
        follow_redirects=False,
    )
    assert quiz.status_code == 303
    quiz_page = client.get(quiz.headers["location"])
    assert quiz_page.status_code == 200
    assert "作答" in quiz_page.text


def _quiz_conversation(conn, base, user_id: int) -> int:
    source = conn.execute(
        "INSERT INTO app.sources (owner_id, platform, account_label) "
        "VALUES (%s, 'learning_space', 'default') RETURNING id",
        (user_id,),
    ).fetchone()
    row = conn.execute(
        "INSERT INTO app.conversations "
        "(source_id, course_id, external_id, title, started_at, mode) "
        "VALUES (%s, %s, %s, '出题练习', now(), 'quiz') RETURNING id",
        (source["id"], base["courses"]["DB"], f"quiz-{user_id}"),
    ).fetchone()
    return row["id"]


def test_theory_session_uses_imported_context(conn, base, tutor_off):
    alice = _student(base, "alice")
    conversation_id = asyncio.run(
        tutor_service.create_session(
            conn,
            alice,
            course_id=base["courses"]["DB"],
            session_mode="theory",
        )
    )
    detail = tutor_service.session_detail(conn, alice, conversation_id)
    assert detail["session"]["mode"] == "theory"
    assert any("理论对话上下文" in message["content"] for message in detail["messages"])

    result = asyncio.run(
        tutor_service.send_theory_message(conn, alice, conversation_id, "隔离级别有哪些？")
    )
    assert "离线规则" in result["message"]["content"]

    with pytest.raises(NotFoundError):
        tutor_service.session_detail(conn, _student(base, "bob"), conversation_id)


def test_quiz_answer_updates_mastery_and_creates_next_question(conn, base, tutor_off):
    alice = _student(base, "alice")
    conversation_id = asyncio.run(
        tutor_service.create_session(
            conn,
            alice,
            course_id=base["courses"]["DB"],
            session_mode="quiz",
            tag_id=base["tag"],
        )
    )
    detail = tutor_service.session_detail(conn, alice, conversation_id)
    assert detail["pending"] is not None
    assert detail["pending"]["knowledge_point"] == "事务"

    with pytest.raises(ValidationError):
        asyncio.run(
            tutor_service.ask_question(
                conn, alice, conversation_id, tag_id=base["tag"]
            )
        )

    first = asyncio.run(
        tutor_service.answer_question(
            conn,
            alice,
            detail["pending"]["id"],
            "事务具有原子性、一致性、隔离性和持久性。",
        )
    )
    assert 1 <= first["score"] <= 10
    assert first["mastery"]["sample_count"] == 1

    asyncio.run(
        tutor_service.ask_question(conn, alice, conversation_id, tag_id=base["tag"])
    )
    pending = tutor_service.session_detail(conn, alice, conversation_id)["pending"]
    assert pending is not None
    asyncio.run(
        tutor_service.answer_question(
            conn,
            alice,
            pending["id"],
            "可重复读通过快照隔离避免幻读，串行化则要求事务完全串行。",
        )
    )

    mastery = conn.execute(
        "SELECT score, last_score, sample_count FROM app.knowledge_mastery "
        "WHERE user_id = %s AND course_id = %s AND tag_id = %s",
        (alice["id"], base["courses"]["DB"], base["tag"]),
    ).fetchone()
    assert mastery["sample_count"] == 2
    assert 1 <= mastery["score"] <= 10

    with pytest.raises(ConflictError):
        asyncio.run(
            tutor_service.answer_question(
                conn, alice, detail["pending"]["id"], "重复提交"
            )
        )


def test_viewer_can_practice_shared_course(conn, base, tutor_off):
    bob = _student(base, "bob")
    conversation_id = asyncio.run(
        tutor_service.create_session(
            conn,
            bob,
            course_id=base["courses"]["DB"],
            session_mode="quiz",
            tag_id=base["tag"],
        )
    )
    pending = tutor_service.session_detail(conn, bob, conversation_id)["pending"]
    assert pending is not None
    result = asyncio.run(
        tutor_service.answer_question(conn, bob, pending["id"], "事务的四个特性是 ACID。")
    )
    assert 1 <= result["score"] <= 10

    mastery = conn.execute(
        "SELECT count(*) AS n FROM app.knowledge_mastery WHERE user_id = %s",
        (bob["id"],),
    ).fetchone()
    assert mastery["n"] == 1


def test_mastery_trigger_uses_ewma(conn, base):
    alice = _student(base, "alice")
    conversation_id = _quiz_conversation(conn, base, alice["id"])
    questions = [
        conn.execute(
            "INSERT INTO app.messages (conversation_id, seq_no, sender, content) "
            "VALUES (%s, %s, 'assistant', %s) RETURNING id",
            (conversation_id, seq, "题目"),
        ).fetchone()["id"]
        for seq in (1, 2)
    ]
    attempt_ids = [
        conn.execute(
            "INSERT INTO app.quiz_attempts "
            "(user_id, course_id, tag_id, conversation_id, question_message_id, reference_answer) "
            "VALUES (%s, %s, %s, %s, %s, '参考答案') RETURNING id",
            (
                alice["id"],
                base["courses"]["DB"],
                base["tag"],
                conversation_id,
                question_id,
            ),
        ).fetchone()["id"]
        for question_id in questions
    ]

    conn.execute(
        "UPDATE app.quiz_attempts SET score = 7, answered_at = now() WHERE id = %s",
        (attempt_ids[0],),
    )
    first = conn.execute(
        "SELECT score, last_score, sample_count FROM app.knowledge_mastery "
        "WHERE user_id = %s AND course_id = %s AND tag_id = %s",
        (alice["id"], base["courses"]["DB"], base["tag"]),
    ).fetchone()
    assert first == {"score": 7, "last_score": 7, "sample_count": 1}

    conn.execute(
        "UPDATE app.quiz_attempts SET score = 9, answered_at = now() WHERE id = %s",
        (attempt_ids[1],),
    )
    second = conn.execute(
        "SELECT score, last_score, sample_count FROM app.knowledge_mastery "
        "WHERE user_id = %s AND course_id = %s AND tag_id = %s",
        (alice["id"], base["courses"]["DB"], base["tag"]),
    ).fetchone()
    assert second == {"score": 8, "last_score": 9, "sample_count": 2}


def test_mastery_requires_linked_knowledge_point_and_membership(conn, base):
    bob = _student(base, "bob")
    orphan = conn.execute(
        "INSERT INTO app.tags (owner_id, name) VALUES (%s, '孤立知识点') RETURNING id",
        (bob["id"],),
    ).fetchone()["id"]
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO app.knowledge_mastery (user_id, course_id, tag_id, score) "
            "VALUES (%s, %s, %s, 5)",
            (bob["id"], base["courses"]["DB"], orphan),
        )

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(
            "INSERT INTO app.knowledge_mastery (user_id, course_id, tag_id, score) "
            "VALUES (%s, %s, %s, 5)",
            (base["users"]["admin"], base["courses"]["DB"], base["tag"]),
        )


def test_app_views_split_theory_and_quiz(conn, base, tutor_off):
    alice = _student(base, "alice")
    conversation_id = asyncio.run(
        tutor_service.create_session(
            conn,
            alice,
            course_id=base["courses"]["DB"],
            session_mode="quiz",
            tag_id=base["tag"],
        )
    )
    quiz = conn.execute(
        "SELECT count(*) AS n FROM app.v_quiz_dialogues WHERE conversation_id = %s",
        (conversation_id,),
    ).fetchone()
    theory = conn.execute(
        "SELECT count(*) AS n FROM app.v_theory_dialogues WHERE conversation_id = %s",
        (conversation_id,),
    ).fetchone()
    assert quiz["n"] == 1
    assert theory["n"] == 0


def test_t2s_views_are_scoped_by_user(conn, base, t2s_conn, tutor_off):
    alice = _student(base, "alice")
    conversation_id = asyncio.run(
        tutor_service.create_session(
            conn,
            alice,
            course_id=base["courses"]["DB"],
            session_mode="quiz",
            tag_id=base["tag"],
        )
    )
    pending = tutor_service.session_detail(conn, alice, conversation_id)["pending"]
    asyncio.run(
        tutor_service.answer_question(
            conn, alice, pending["id"], "事务具有原子性、一致性、隔离性和持久性。"
        )
    )

    t2s_conn.execute("SELECT set_config('app.user_id', %s, true)", (str(alice["id"]),))
    quiz_rows = t2s_conn.execute(
        "SELECT count(*) AS n FROM t2s.v_my_quiz_conversations"
    ).fetchone()["n"]
    mastery_rows = t2s_conn.execute(
        "SELECT count(*) AS n FROM t2s.v_my_knowledge_mastery"
    ).fetchone()["n"]
    assert quiz_rows >= 1
    assert mastery_rows >= 1
    t2s_conn.rollback()

    t2s_conn.execute(
        "SELECT set_config('app.user_id', %s, true)", (str(base["users"]["admin"]),)
    )
    assert (
        t2s_conn.execute(
            "SELECT count(*) AS n FROM t2s.v_my_quiz_conversations"
        ).fetchone()["n"]
        == 0
    )
    assert (
        t2s_conn.execute(
            "SELECT count(*) AS n FROM t2s.v_my_knowledge_mastery"
        ).fetchone()["n"]
        == 0
    )
    t2s_conn.rollback()
