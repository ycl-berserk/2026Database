from __future__ import annotations

from app.config import settings
from app.services import text2sql_eval_service as evals


def test_evaluate_runs_gold_generated_and_security_cases(conn, t2s_conn, base):
    cases = {
        "questions": [
            {
                "id": "Q1",
                "question": "每个课程有多少会话和消息？",
                "user": "alice",
                "gold_sql": "SELECT code, name, conversation_count, message_count, "
                "annotation_count FROM v_my_course_stats "
                "ORDER BY message_count DESC",
            },
            {
                "id": "Q2",
                "question": "今天到期的复习项",
                "user": "alice",
                "gold_sql": "SELECT annotation_content, annotation_kind, course_name, "
                "due_date, status FROM v_my_review_items "
                "WHERE status <> 'mastered' AND due_date <= current_date "
                "ORDER BY due_date",
            },
        ],
        "guard_cases": [
            {"id": "G1", "sql": "DROP TABLE app.users", "reason": "DDL"},
            {"id": "G2", "sql": "DELETE FROM app.messages", "reason": "DML"},
        ],
        "scope_cases": [
            {
                "id": "S1",
                "user": "admin",
                "sql": "SELECT conversation_id FROM v_my_conversations",
                "expect_empty": True,
            }
        ],
    }

    original_mode = settings.text2sql_mode
    report = evals.evaluate(cases, conn, t2s_conn, provider="mock")

    assert settings.text2sql_mode == original_mode
    assert report["questions"]["total"] == 2
    assert report["questions"]["matched"] == 2
    assert report["legality"]["gold_rate"] == 1.0
    assert report["legality"]["generated_rate"] == 1.0
    assert report["security"] == {"total": 3, "passed": 3, "rate": 1.0}
    assert report["scope_cases"][0]["passed"] is True
    assert all(case["match"] for case in report["cases"])
