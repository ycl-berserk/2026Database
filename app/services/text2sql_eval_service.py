from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import settings
from . import text2sql_service as t2s


def load_cases(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("questions", "guard_cases", "scope_cases"):
        if not isinstance(payload.get(key), list):
            raise ValueError(f"评测集缺少 {key} 列表")
    return payload


def _canonical(result: dict | None) -> list[tuple]:
    if result is None:
        return []
    columns = sorted(result["columns"])
    rows = [
        tuple(repr(row.get(column)) for column in columns)
        for row in result["rows"]
    ]
    return sorted(rows)


def _run_sql(t2s_conn, sql: str, user_id: int) -> dict:
    safe_sql = t2s.check_sql(sql)
    plan = t2s.preflight_query(t2s_conn, safe_sql, user_id)
    result = t2s.execute_query(t2s_conn, safe_sql, user_id)
    return {"sql": safe_sql, "plan": plan, "result": result}


def _user_ids(conn, usernames: set[str]) -> dict[str, int]:
    rows = conn.execute(
        "SELECT username, id FROM app.users WHERE username = ANY(%s)",
        (list(usernames),),
    ).fetchall()
    return {row["username"]: row["id"] for row in rows}


def _scope_passes(result: dict, case: dict) -> bool:
    if case.get("expect_empty"):
        return not result["rows"]
    forbidden = {str(value) for value in case.get("forbidden_values", [])}
    if not forbidden:
        return True
    return all(not any(str(value) in forbidden for value in row.values()) for row in result["rows"])


def evaluate(cases: dict, main_conn, t2s_conn, provider: str = "mock") -> dict:
    usernames = {case.get("user", "alice") for case in cases["questions"]}
    usernames.update(case.get("user", "alice") for case in cases["scope_cases"])
    users = _user_ids(main_conn, usernames)

    original_mode = settings.text2sql_mode
    if provider and provider != "auto":
        object.__setattr__(settings, "text2sql_mode", provider)
    started = time.perf_counter()
    try:
        question_cases = []
        modes: Counter[str] = Counter()
        for case in cases["questions"]:
            user_id = users[case.get("user", "alice")]
            item: dict[str, Any] = {
                "id": case["id"],
                "question": case["question"],
                "user": case.get("user", "alice"),
                "gold_sql": case["gold_sql"],
            }
            gold_ok = False
            gold_result = None
            try:
                gold = _run_sql(t2s_conn, case["gold_sql"], user_id)
                gold_ok = True
                gold_result = gold["result"]
            except Exception as exc:
                item["gold_status"] = type(exc).__name__
                item["gold_error"] = str(exc)[:300]

            item["status"] = "gold_error"
            item["match"] = False
            item["generated_sql"] = None
            item["latency_ms"] = None
            if gold_ok:
                case_started = time.perf_counter()
                try:
                    sql, explanation, mode = asyncio.run(
                        t2s.generate_sql(case["question"])
                    )
                    modes[mode] += 1
                    item["mode"] = mode
                    item["explanation"] = explanation
                    generated = _run_sql(t2s_conn, sql, user_id)
                    item["generated_sql"] = generated["sql"]
                    item["status"] = "ok"
                    item["match"] = _canonical(gold_result) == _canonical(
                        generated["result"]
                    )
                    item["generated_rows"] = len(generated["result"]["rows"])
                except t2s.GuardError as exc:
                    item["status"] = "guard"
                    item["error"] = exc.message
                except t2s.PreflightError as exc:
                    item["status"] = "preflight"
                    item["error"] = exc.message
                except Exception as exc:
                    item["status"] = "error"
                    item["error"] = f"{type(exc).__name__}: {exc}"[:300]
                item["latency_ms"] = int(
                    (time.perf_counter() - case_started) * 1000
                )
            question_cases.append(item)

        guard_cases = []
        for case in cases["guard_cases"]:
            blocked = False
            error = None
            try:
                t2s.check_sql(case["sql"])
            except t2s.GuardError as exc:
                blocked = True
                error = exc.message
            guard_cases.append(
                {
                    "id": case["id"],
                    "reason": case.get("reason", ""),
                    "blocked": blocked,
                    "error": error,
                }
            )

        scope_cases = []
        for case in cases["scope_cases"]:
            user_id = users[case.get("user", "alice")]
            passed = False
            detail = ""
            try:
                result = t2s.execute_query(
                    t2s_conn, t2s.check_sql(case["sql"]), user_id
                )
                passed = _scope_passes(result, case)
                detail = f"{len(result['rows'])} 行"
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"[:200]
            scope_cases.append(
                {
                    "id": case["id"],
                    "user": case.get("user", "alice"),
                    "reason": case.get("reason", ""),
                    "passed": passed,
                    "detail": detail,
                }
            )
    finally:
        object.__setattr__(settings, "text2sql_mode", original_mode)

    total = len(question_cases)
    matched = sum(1 for item in question_cases if item["match"])
    gold_legal = sum(1 for item in question_cases if item["status"] != "gold_error")
    generated_legal = sum(
        1 for item in question_cases if item["status"] not in ("guard", "gold_error")
    )
    security_total = len(guard_cases) + len(scope_cases)
    security_passed = sum(1 for case in guard_cases if case["blocked"]) + sum(
        1 for case in scope_cases if case["passed"]
    )
    latencies = [
        item["latency_ms"]
        for item in question_cases
        if item["latency_ms"] is not None
    ]
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "provider": provider,
        "modes": dict(modes),
        "questions": {
            "total": total,
            "matched": matched,
            "accuracy": round(matched / total, 4) if total else 0,
            "gold_legal": gold_legal,
            "generated_legal": generated_legal,
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1)
            if latencies
            else None,
        },
        "legality": {
            "gold_rate": round(gold_legal / total, 4) if total else 0,
            "generated_rate": round(generated_legal / total, 4) if total else 0,
        },
        "security": {
            "total": security_total,
            "passed": security_passed,
            "rate": round(security_passed / security_total, 4)
            if security_total
            else 0,
        },
        "guard_cases": guard_cases,
        "scope_cases": scope_cases,
        "cases": question_cases,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }
