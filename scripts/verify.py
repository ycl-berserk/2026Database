"""端到端验收：只读检查 + 写入检查（最终整体回滚），输出 PASS/FAIL 与 JSON 报告。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

import psycopg
from psycopg.rows import dict_row

from app.auth import verify_password
from app.config import settings
from app.errors import AuthRequired, NotFoundError, PermissionDenied, RateLimited
from app.permissions import get_course_role, require_conversation_edit
from app.services import (
    annotation_service,
    conversation_service,
    embedding_service,
    export_service,
    import_service,
    review_service,
    search_service,
    stats_service,
    text2sql_eval_service,
    text2sql_service,
    token_service,
)

ALICE = {"username": "alice", "password": "Alice123!"}
BOB = {"username": "bob", "password": "Bob123!"}
ADMIN = {"username": "admin", "password": "Admin123!"}


class Rollback(Exception):
    pass


class Report:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def run(self, key: str, label: str, fn) -> bool:
        started = time.perf_counter()
        try:
            detail = fn()
            status = "PASS"
        except Exception as exc:  # noqa: BLE001 - 验收脚本需要收集所有失败
            detail = f"{type(exc).__name__}: {exc}"
            status = "FAIL"
        elapsed = int((time.perf_counter() - started) * 1000)
        self.checks.append(
            {"key": key, "label": label, "status": status,
             "detail": str(detail)[:500], "elapsed_ms": elapsed}
        )
        mark = "PASS" if status == "PASS" else "FAIL"
        print(f"[{mark}] {label}" + (f" — {detail}" if status == "FAIL" else ""))
        return status == "PASS"

    @property
    def passed(self) -> int:
        return sum(1 for check in self.checks if check["status"] == "PASS")

    @property
    def failed(self) -> int:
        return sum(1 for check in self.checks if check["status"] == "FAIL")


def _ok(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _user(conn, username: str) -> dict:
    row = conn.execute(
        "SELECT id, username, display_name, system_role, is_active, password_hash "
        "FROM app.users WHERE username = %s",
        (username,),
    ).fetchone()
    _ok(row is not None, f"缺少演示用户 {username}")
    return row


def _count(conn, sql: str, params=None) -> int:
    return conn.execute(sql, params or ()).fetchone()["n"]


def _snapshot(conn) -> dict[str, int]:
    return {
        table: _count(conn, f"SELECT count(*) AS n FROM app.{table}")
        for table in (
            "users", "courses", "course_members", "sources", "conversations",
            "messages", "tags", "conversation_tags", "annotations",
            "review_items", "review_logs", "import_batches", "text2sql_log",
            "api_tokens",
        )
    }


def build_checks(conn, t2s_conn, report: Report) -> dict:
    state: dict = {}
    alice = _user(conn, "alice")
    bob = _user(conn, "bob")
    admin = _user(conn, "admin")
    state["users"] = {"alice": alice, "bob": bob, "admin": admin}
    alice_ctx = {"id": alice["id"], "system_role": "student"}
    bob_ctx = {"id": bob["id"], "system_role": "student"}

    def schema_tables():
        tables = _count(
            conn,
            "SELECT count(*) AS n FROM information_schema.tables WHERE table_schema = 'app'",
        )
        views = _count(
            conn,
            "SELECT count(*) AS n FROM pg_views WHERE schemaname = 't2s'",
        )
        _ok(tables == 14, f"app 表数量应为 14，实际 {tables}")
        _ok(views == 13, f"t2s 视图数量应为 13，实际 {views}")
        return f"14 张表、13 个权限视图"
    report.run("schema.tables", "Schema：14 张业务表与 13 个 t2s 视图", schema_tables)

    def schema_constraints():
        constraints = {
            row["conname"]
            for row in conn.execute(
                "SELECT conname FROM pg_constraint WHERE connamespace = 'app'::regnamespace"
            ).fetchall()
        }
        required = {
            "users_username_key",
            "messages_conversation_seq_unique",
            "review_items_user_annotation_unique",
            "conversations_source_external_unique",
            "sources_owner_platform_label_unique",
            "import_batches_preview_token_key",
            "review_items_status_check",
            "review_logs_event_unique",
            "annotations_client_event_unique",
            "api_tokens_hash_unique",
            "api_tokens_name_check",
            "messages_sender_check",
        }
        missing = required - constraints
        _ok(not missing, f"缺少约束：{sorted(missing)}")
        indexes = {
            row["indexname"]
            for row in conn.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'app'"
            ).fetchall()
        }
        required_indexes = {
            "course_members_one_owner",
            "messages_content_trgm",
            "messages_content_md5_idx",
            "conversations_title_trgm",
            "review_items_queue_idx",
            "api_tokens_user_idx",
        }
        missing_idx = required_indexes - indexes
        _ok(not missing_idx, f"缺少索引：{sorted(missing_idx)}")
        triggers = {
            row["tgname"]
            for row in conn.execute(
                "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"
            ).fetchall()
        }
        required_triggers = {
            "trg_courses_owner_required",
            "trg_conversation_tags_access",
            "trg_annotations_access",
            "trg_review_items_access",
            "trg_annotations_updated_at",
        }
        missing_trg = required_triggers - triggers
        _ok(not missing_trg, f"缺少触发器：{sorted(missing_trg)}")
        return "唯一键、CHECK、部分唯一索引与 5 个触发器齐全"
    report.run("schema.constraints", "Schema：主外键、唯一/检查约束、索引与触发器", schema_constraints)

    def index_health():
        conn.execute("SET LOCAL enable_seqscan = off")
        try:
            rows = conn.execute("SELECT id, owner_id, name FROM app.tags ORDER BY id").fetchall()
            missing = []
            for row in rows:
                found = {
                    hit["id"]
                    for hit in conn.execute(
                        "SELECT id FROM app.tags WHERE owner_id = %s AND name = %s",
                        (row["owner_id"], row["name"]),
                    ).fetchall()
                }
                if row["id"] not in found:
                    missing.append(row["name"])
        finally:
            conn.execute("SET LOCAL enable_seqscan = on")
        _ok(
            not missing,
            f"唯一索引与实际数据不一致（常见于镜像/libc 排序规则变更）：{missing[:5]}",
        )
        return f"{len(rows)} 个标签均可通过 tags_owner_name_unique 命中"
    report.run("schema.index_health", "Schema：索引与数据一致性（排序规则变更防护）", index_health)

    def demo_counts():
        expected = {
            "users": 3, "courses": 5, "course_members": 7, "sources": 4,
            "conversations": 30, "messages": 420, "tags": 59,
            "annotations": 66, "review_items": 39,
        }
        actual = {table: _count(conn, f"SELECT count(*) AS n FROM app.{table}")
                  for table in expected}
        _ok(actual == expected, f"演示数据计数不符：{actual} != {expected}")
        return "3 用户 / 5 课程 / 30 会话 / 420 消息 / 66 批注 / 39 复习项"
    report.run("data.counts", "演示数据规模与来源标注", demo_counts)

    def import_batches():
        rows = conn.execute(
            "SELECT status, count(*) AS n FROM app.import_batches GROUP BY status"
        ).fetchall()
        _ok(rows == [{"status": "imported", "n": 5}], f"导入批次状态异常：{rows}")
        leaked = _count(
            conn,
            "SELECT count(*) AS n FROM app.import_batches "
            "WHERE status <> 'preview' AND payload IS NOT NULL",
        )
        _ok(leaked == 0, "导入完成后仍保留预览 payload")
        return "5 个批次均已导入，payload 已清理"
    report.run("data.import_batches", "导入批次：状态与 payload 清理", import_batches)

    def inbox_seed():
        unclassified = conversation_service.list_conversations(
            conn, alice["id"], course_id="none"
        )
        _ok(unclassified["total"] == 3, f"收件箱应有 3 个未归类会话，实际 {unclassified['total']}")
        _ok(
            all(row["course_code"] is None for row in unclassified["rows"]),
            "收件箱列表不应出现已归类会话",
        )
        return "收件箱保留 3 个未归类会话，供批量整理演示"
    report.run("data.inbox", "演示数据：收件箱未归类会话", inbox_seed)

    def auth_passwords():
        for account in (ALICE, BOB, ADMIN):
            row = _user(conn, account["username"])
            _ok(verify_password(account["password"], row["password_hash"]),
                f"{account['username']} 演示密码校验失败")
            _ok(not verify_password(account["password"] + "x", row["password_hash"]),
                f"{account['username']} 错误密码竟然通过")
        return "PBKDF2 哈希校验通过，错误密码被拒绝"
    report.run("auth.passwords", "认证：演示账号密码哈希与校验", auth_passwords)

    def roles():
        db_course = conn.execute("SELECT id FROM app.courses WHERE code = 'DB'").fetchone()["id"]
        os_course = conn.execute("SELECT id FROM app.courses WHERE code = 'OS'").fetchone()["id"]
        _ok(get_course_role(conn, db_course, alice["id"]) == "owner", "alice 应为 DB 课程 owner")
        _ok(get_course_role(conn, db_course, bob["id"]) == "viewer", "bob 应为 DB viewer")
        _ok(get_course_role(conn, os_course, bob["id"]) == "editor", "bob 应为 OS editor")
        return "owner/editor/viewer 三种课程角色判定正确"
    report.run("auth.roles", "权限：课程角色 owner/editor/viewer", roles)

    def scopes():
        alice_rows = conversation_service.list_conversations(conn, alice["id"])
        bob_rows = conversation_service.list_conversations(conn, bob["id"])
        _ok(alice_rows["total"] == 30, f"alice 可访问会话应为 30，实际 {alice_rows['total']}")
        _ok(bob_rows["total"] == 12, f"bob 可访问会话应为 12，实际 {bob_rows['total']}")
        return "alice 30 个会话，bob 仅看到共享课程中的 12 个"
    report.run("auth.scope", "权限：会话可见范围按所有者/课程成员过滤", scopes)

    def viewer_blocked():
        net_conversation = conn.execute(
            "SELECT v.id FROM app.conversations v JOIN app.courses c ON c.id = v.course_id "
            "WHERE c.code = 'NET' ORDER BY v.id LIMIT 1"
        ).fetchone()["id"]
        state["net_conversation"] = net_conversation
        with_error = None
        try:
            require_conversation_edit(conn, net_conversation, bob_ctx)
        except PermissionDenied as exc:
            with_error = exc
        _ok(with_error is not None, "bob 不应能编辑未共享的 NET 会话")
        try:
            conversation_service.get_conversation(conn, net_conversation, bob_ctx)
        except NotFoundError:
            hidden = True
        else:
            hidden = False
        _ok(hidden, "bob 不应能读取未共享的 NET 会话")
        return "viewer / 非成员写操作与读取均被拒绝"
    report.run("auth.denied", "权限：越权编辑与读取被拦截", viewer_blocked)

    def import_duplicate_preview():
        raw = (ROOT / "data" / "samples" / "duplicate_db.json").read_bytes()
        result = import_service.parse_upload(
            "duplicate_db.json", raw, platform=None, account_label=None
        )
        _ok(result.ok, f"重复样例解析失败：{result.errors[:2]}")
        source_id = import_service.get_or_create_source(
            conn, alice["id"], result.platform, result.account_label
        )
        saved = import_service.save_preview(
            conn, user_id=alice["id"], source_id=source_id,
            file_name="duplicate_db.json", raw=raw, result=result,
        )
        batch = import_service.load_batch(conn, str(saved["preview_token"]), alice["id"])
        context = import_service.preview_context(conn, batch, alice["id"])
        _ok(context["counts"] == {"new": 0, "update": 0, "duplicate": 6},
            f"重复文件预览分类异常：{context['counts']}")
        state["dup_batch"] = batch
        return "六个会话全部识别为 duplicate（内容哈希一致）"
    report.run("import.preview", "导入：重复文件预览与查重分类", import_duplicate_preview)

    def import_duplicate_confirm():
        batch = state.get("dup_batch")
        _ok(batch is not None, "缺少重复文件预览批次")
        counts = import_service.confirm_import(
            conn,
            token=str(batch["preview_token"]),
            user=alice_ctx,
            selected=set(range(6)),
            course_map={},
        )
        _ok(counts["conversations_new"] == 0, "重复导入不应新增会话")
        _ok(counts["messages_new"] == 0, "重复导入不应新增消息")
        _ok(counts["messages_skipped"] == 84, f"应跳过 84 条重复消息，实际 {counts['messages_skipped']}")
        _ok(counts["conflicts"] == 0, "相同内容不应产生冲突")
        return "重复确认新增 0 会话 / 0 消息，跳过 84 条"
    report.run("import.confirm", "导入：确认入库幂等（ON CONFLICT 去重）", import_duplicate_confirm)

    def broken_file():
        raw = (ROOT / "data" / "samples" / "broken.json").read_bytes()
        result = import_service.parse_upload("broken.json", raw, platform=None, account_label=None)
        _ok(not result.ok, "损坏样例不应通过解析")
        _ok(bool(result.errors), "损坏样例缺少错误信息")
        return f"解析被拒绝，共 {len(result.errors)} 条错误"
    report.run("import.broken", "导入：损坏文件校验拦截", broken_file)

    def platform_adapters():
        samples = ROOT / "data" / "samples"
        expected = {
            "chatgpt_export_conversations.json": ("chatgpt", 2, 28),
            "qoder_sessions_export.json": ("qoder", 2, 28),
            "deepseek_export.md": ("deepseek", 2, 28),
        }
        for name, (platform, conversations, messages) in expected.items():
            result = import_service.parse_upload(
                name, (samples / name).read_bytes(),
                platform=None, account_label=None,
            )
            _ok(result.ok, f"{name} 解析失败：{result.errors[:2]}")
            _ok(result.platform == platform, f"{name} 平台应为 {platform}，实际 {result.platform}")
            _ok(
                len(result.conversations) == conversations
                and result.message_count == messages,
                f"{name} 应解析 {conversations} 会话 / {messages} 消息，"
                f"实际 {len(result.conversations)} / {result.message_count}",
            )
        return "ChatGPT mapping、Qoder sessions、DeepSeek Markdown 均映射为统一格式"
    report.run("import.adapters", "导入：平台导出适配器（ChatGPT / Qoder / DeepSeek）", platform_adapters)

    def keyword_search():
        result = conversation_service.list_conversations(conn, alice["id"], q="事务")
        _ok(result["total"] >= 4, f"关键词“事务”命中过少：{result['total']}")
        hit = next((row for row in result["rows"] if row["hit_content"]), None)
        _ok(hit is not None, "关键词命中缺少消息片段")
        _ok("事务" in hit["snippet"], "消息片段未包含关键词")
        return f"命中 {result['total']} 个会话，片段包含关键词"
    report.run("search.keyword", "检索：中文关键词命中消息并生成片段", keyword_search)

    def filters():
        db_course = conn.execute("SELECT id FROM app.courses WHERE code = 'DB'").fetchone()["id"]
        by_course = conversation_service.list_conversations(
            conn, alice["id"], course_id=str(db_course)
        )
        _ok(by_course["total"] == 6, f"DB 课程会话应为 6，实际 {by_course['total']}")
        tag = conn.execute(
            "SELECT id FROM app.tags WHERE owner_id = %s AND name = '事务'", (alice["id"],)
        ).fetchone()
        if tag is not None:
            by_tag = conversation_service.list_conversations(conn, alice["id"], tag_id=tag["id"])
            _ok(by_tag["total"] >= 1, "标签筛选没有结果")
        return "课程筛选 6 个会话，标签筛选正常"
    report.run("search.filters", "检索：课程 / 标签筛选", filters)

    def hybrid_search():
        extension = conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
        _ok(extension is not None, "pgvector 扩展未安装")
        index = conn.execute(
            "SELECT 1 FROM pg_indexes WHERE schemaname = 'app' "
            "AND indexname = 'messages_embedding_hnsw'"
        ).fetchone()
        _ok(index is not None, "缺少 messages_embedding_hnsw 索引")

        rows = conn.execute(
            """
            SELECT DISTINCT ON (v.id) v.id AS conversation_id, m.id AS message_id
              FROM app.conversations v
              JOIN app.sources s ON s.id = v.source_id
              JOIN app.messages m ON m.conversation_id = v.id
             WHERE s.owner_id = %s
             ORDER BY v.id, m.id
            """,
            (alice["id"],),
        ).fetchall()
        _ok(len(rows) >= 2, "演示数据不足以测试混合检索")
        first, second = rows[0], rows[1]
        dimension = settings.embedding_dim
        vectors = []
        for index_value in (0, 1):
            vector = [0.0] * dimension
            vector[index_value] = 1.0
            vectors.append(vector)

        def store(row, vector):
            conn.execute(
                """
                UPDATE app.messages
                   SET embedding = %s::vector, embedding_model = %s, embedded_at = now()
                 WHERE id = %s
                """,
                (embedding_service.vector_literal(vector), settings.embedding_model,
                 row["message_id"]),
            )

        try:
            store(first, vectors[0])
            store(second, vectors[1])
            query = "zzz_hybrid_probe_不存在的关键词"
            result = conversation_service.list_conversations(
                conn, alice["id"], q=query, query_vector=vectors[0]
            )
            _ok(result["retrieval"]["mode"] == "hybrid", "混合检索未启用")
            _ok(result["total"] >= 2, f"向量候选过少：{result['total']}")
            _ok(
                result["rows"][0]["id"] == first["conversation_id"],
                "最近向量会话未排在首位",
            )
            _ok(result["rows"][0]["hit_mode"] == "vector", "命中未标记为向量")
            admin_result = conversation_service.list_conversations(
                conn, admin["id"], q=query, query_vector=vectors[0]
            )
            _ok(admin_result["total"] == 0, "管理员不应检索到他人向量结果")
        finally:
            conn.execute(
                "UPDATE app.messages SET embedding = NULL, embedding_model = NULL, "
                "embedded_at = NULL WHERE id = ANY(%s)",
                ([first["message_id"], second["message_id"]],),
            )
        return "pgvector + HNSW 生效，RRF 命中向量会话，管理员 0 结果"
    report.run("search.hybrid", "搜索：pgvector 混合检索与范围隔离", hybrid_search)

    def context_reading():
        conversation_id = conn.execute(
            "SELECT v.id FROM app.conversations v JOIN app.courses c ON c.id = v.course_id "
            "WHERE c.code = 'DB' ORDER BY v.id LIMIT 1"
        ).fetchone()["id"]
        state["db_conversation"] = conversation_id
        detail = conversation_service.conversation_detail(conn, conversation_id, alice_ctx)
        _ok(detail["counts"]["total"] == 14, f"会话消息数应为 14，实际 {detail['counts']['total']}")
        _ok(detail["counts"]["user_count"] + detail["counts"]["assistant_count"] == 14,
            "提问/回答计数不等于总消息数")
        note_count = sum(len(message["annotations"]) for message in detail["messages"])
        _ok(note_count >= 2, f"种子批注数量异常：{note_count}")
        return "14 条消息按顺序展示，批注内联返回"
    report.run("search.context", "阅读：会话上下文、发送者统计与批注内联", context_reading)

    def stats_overview():
        stats = stats_service.load_stats(conn, alice["id"])
        overview = stats["overview"]
        _ok(overview["course_count"] == 5, "课程数统计错误")
        _ok(overview["conversation_count"] == 30, "会话数统计错误")
        _ok(overview["message_count"] == 420, "消息数统计错误")
        _ok(overview["annotation_count"] == 66, "批注数统计错误")
        _ok(overview["tag_count"] == 59, f"标签数统计错误：{overview['tag_count']}")
        summary = stats["review_summary"]
        _ok(summary["total"] == 35 and summary["mastered"] == 7, f"复习统计错误：{summary}")
        _ok(len(stats["courses"]) == 5, "课程统计行数错误")
        _ok(len(stats["activity"]) == 8, "月度活跃应补齐 8 个月")
        return "概览、5 门课程、8 个月活跃与复习摘要一致"
    report.run("stats.overview", "统计：概览、课程、月度与复习聚合", stats_overview)

    def stats_csv():
        data = stats_service.load_stats(conn, alice["id"])
        text = stats_service.stats_csv(data)
        _ok(text.startswith("\ufeff"), "CSV 缺少 UTF-8 BOM")
        _ok("section,item,metric,value" in text, "CSV 表头缺失")
        _ok("DB 数据库系统概论" in text, "CSV 缺少课程统计")
        return f"CSV 导出 {len(text.splitlines())} 行"
    report.run("stats.csv", "导出：统计 CSV（UTF-8 BOM）", stats_csv)

    def markdown_export():
        conversation_id = state.get("db_conversation")
        _ok(conversation_id is not None, "缺少会话上下文")
        filename, text = export_service.conversation_markdown(conn, conversation_id, alice_ctx)
        _ok(filename.endswith(".md"), "Markdown 文件名不正确")
        _ok(text.startswith("# "), "Markdown 缺少会话标题")
        _ok("演示数据不含真实个人信息" in text, "Markdown 缺少模拟数据声明")
        _ok("## " in text, "Markdown 缺少消息分段")
        return f"导出 {filename}，{len(text.splitlines())} 行"
    report.run("export.markdown", "导出：单个会话 Markdown（含模拟数据声明）", markdown_export)

    def annotation_lifecycle():
        conversation_id = state.get("db_conversation")
        _ok(conversation_id is not None, "缺少会话上下文")
        message_id = conn.execute(
            "SELECT id FROM app.messages WHERE conversation_id = %s AND sender = 'user' "
            "ORDER BY seq_no LIMIT 1",
            (conversation_id,),
        ).fetchone()["id"]
        created = annotation_service.create_annotation(
            conn, message_id, alice_ctx, "question", "验收脚本创建的临时批注"
        )
        _ok(annotation_service.add_to_review(conn, created["id"], alice_ctx) is True,
            "临时批注未能加入复习队列")
        review = conn.execute(
            "SELECT id FROM app.review_items WHERE annotation_id = %s AND user_id = %s",
            (created["id"], alice["id"]),
        ).fetchone()
        _ok(review is not None, "复习项未写入")
        event_id = str(uuid4())
        first = review_service.review_once(
            conn, review["id"], alice["id"], event_id=event_id
        )
        _ok(first["interval_days"] == 2, f"首次复习间隔应为 2 天，实际 {first['interval_days']}")
        replay = review_service.review_once(
            conn, review["id"], alice["id"], event_id=event_id
        )
        _ok(replay["duplicate"] is True, "离线事件重放未被识别为幂等")
        _ok(replay["review_count"] == first["review_count"], "重放事件重复累加复习次数")
        second = review_service.review_once(conn, review["id"], alice["id"])
        _ok(second["interval_days"] == 4, f"第二次复习间隔应为 4 天，实际 {second['interval_days']}")
        log_count = _count(
            conn,
            "SELECT count(*) AS n FROM app.review_logs WHERE review_item_id = %s",
            (review["id"],),
        )
        _ok(log_count == 2, f"复习事件应为 2 条（重放不落库），实际 {log_count}")
        return "批注 → 复习项 → 间隔 2/4 天递增，事件幂等落库"
    report.run("annotation.lifecycle", "批注与复习：创建、入队、间隔翻倍", annotation_lifecycle)

    def review_activity():
        activity = review_service.activity(conn, alice["id"], days=35)
        _ok(len(activity) == 35, f"热力图应补齐 35 天，实际 {len(activity)}")
        total = sum(row["count"] for row in activity)
        _ok(total > 0, "演示数据缺少复习事件")
        _ok(all(0 <= row["level"] <= 4 for row in activity), "热力图等级越界")
        return f"近 5 周 {total} 次复习事件，35 天连续补齐"
    report.run("review.activity", "复习：事件流水与 5 周热力图", review_activity)

    def t2s_mock_run():
        result = asyncio.run(
            text2sql_service.run("我的复习队列", alice["id"], t2s_conn, conn)
        )
        _ok(result["mode"] == "mock", f"未配置 LLM 时应为 mock，实际 {result['mode']}")
        _ok(result["row_count"] == 28, f"alice 待复习应为 28 条，实际 {result['row_count']}")
        _ok(result["columns"][0] == "annotation_content", "结果列不符合预期")
        return "自然语言→SQL→执行返回 28 条复习项（mock 模式）"
    report.run("t2s.run", "Text2SQL：mock 模式查询与结果", t2s_mock_run)

    def t2s_scope():
        sql = "SELECT conversation_id FROM v_my_conversations"
        alice_rows = text2sql_service.execute_query(t2s_conn, sql, alice["id"])["rows"]
        bob_rows = text2sql_service.execute_query(t2s_conn, sql, bob["id"])["rows"]
        admin_rows = text2sql_service.execute_query(t2s_conn, sql, admin["id"])["rows"]
        _ok(len(alice_rows) == 30, f"alice 视图应返回 30 行，实际 {len(alice_rows)}")
        _ok(len(bob_rows) == 12, f"bob 视图应返回 12 行，实际 {len(bob_rows)}")
        _ok(admin_rows == [], "管理员不应看到他人会话")
        return "同一 SQL 下 alice=30 / bob=12 / admin=0，行级过滤生效"
    report.run("t2s.scope", "Text2SQL：t2s 视图按当前用户过滤", t2s_scope)

    def t2s_guard():
        cases = [
            "DROP TABLE app.users",
            "SELECT 1; DELETE FROM app.messages",
            "SELECT * FROM app.users",
            "SELECT * FROM pg_catalog.pg_tables",
            "SELECT current_user",
            "SELECT * FROM v_my_courses -- comment",
            "WITH x AS (DELETE FROM app.users RETURNING id) SELECT * FROM x",
            "SELECT pg_read_file('/etc/passwd')",
            "SELECT * FROM v_my_courses FOR UPDATE",
            "SELECT * FROM generate_series(1, 5)",
        ]
        blocked = 0
        for sql in cases:
            try:
                text2sql_service.check_sql(sql)
            except text2sql_service.GuardError:
                blocked += 1
        _ok(blocked == len(cases), f"SQL 守卫漏放 {len(cases) - blocked} 条危险语句")
        safe = "SELECT title FROM v_my_conversations WHERE title ILIKE '%delete%'"
        _ok(text2sql_service.check_sql(safe) == safe, "AST 守卫误伤字符串字面量")
        return f"{len(cases)} 条危险 SQL 全部被拒绝，正常字面量未被误伤"
    report.run("t2s.guard", "Text2SQL：单语句 / 只读 / 白名单守卫", t2s_guard)

    def t2s_preflight():
        plan = text2sql_service.preflight_query(
            t2s_conn, "SELECT code, name FROM v_my_courses", alice["id"]
        )
        _ok(plan is not None, "预检未返回执行计划")
        _ok(plan["total_cost"] > 0, "预检总代价应大于 0")
        _ok(plan["plan_rows"] >= 1, "预检估算行数应为正数")

        original = settings.text2sql_max_cost
        caught = False
        try:
            object.__setattr__(settings, "text2sql_max_cost", 0.01)
            try:
                text2sql_service.preflight_query(
                    t2s_conn,
                    "SELECT a.message_id FROM v_my_messages a "
                    "CROSS JOIN v_my_messages b",
                    alice["id"],
                )
            except text2sql_service.PreflightError:
                caught = True
        finally:
            object.__setattr__(settings, "text2sql_max_cost", original)
        _ok(caught, "高成本查询未被预检拦截")
        return f"正常查询代价 {plan['total_cost']}，高成本笛卡尔积被拒绝"
    report.run("t2s.preflight", "Text2SQL：EXPLAIN 预检拦截高成本查询", t2s_preflight)

    def t2s_eval():
        cases = text2sql_eval_service.load_cases(ROOT / "data" / "text2sql_eval.json")
        result = text2sql_eval_service.evaluate(cases, conn, t2s_conn, provider="mock")
        questions = result["questions"]
        security = result["security"]
        _ok(
            result["legality"]["gold_rate"] == 1.0,
            f"金标准 SQL 合法率应为 100%，实际 {result['legality']['gold_rate']}",
        )
        _ok(
            result["legality"]["generated_rate"] == 1.0,
            f"生成 SQL 合法率应为 100%，实际 {result['legality']['generated_rate']}",
        )
        _ok(
            security["rate"] == 1.0,
            f"越权拦截率应为 100%，实际 {security['passed']}/{security['total']}",
        )
        _ok(
            questions["accuracy"] >= 0.5,
            f"mock 执行准确率应不低于 50%，实际 {questions['accuracy']}",
        )
        return (
            f"30 题金标准全部合法，mock 准确率 "
            f"{questions['accuracy'] * 100:.1f}%，越权拦截 "
            f"{security['passed']}/{security['total']}"
        )
    report.run("t2s.eval", "Text2SQL：金标准评测集与越权拦截", t2s_eval)

    def t2s_rejected_log():
        original = text2sql_service.generate_sql

        async def evil(_question: str):
            return "DELETE FROM app.messages", "危险语句", "mock"

        text2sql_service.generate_sql = evil
        try:
            caught = False
            try:
                asyncio.run(
                    text2sql_service.run("删除消息", alice["id"], t2s_conn, conn)
                )
            except text2sql_service.GuardError:
                caught = True
            _ok(caught, "危险 SQL 未被运行时守卫拦截")
        finally:
            text2sql_service.generate_sql = original
        log = text2sql_service.history(conn, alice["id"], limit=1)[0]
        _ok(log["status"] == "rejected", f"日志状态应为 rejected，实际 {log['status']}")
        return "危险 SQL 被拒绝并写入 rejected 审计日志"
    report.run("t2s.audit", "Text2SQL：危险 SQL 拒绝与审计日志", t2s_rejected_log)

    def t2s_reader_blocked():
        blocked = False
        try:
            t2s_conn.execute("SELECT count(*) FROM app.users")
        except psycopg.errors.InsufficientPrivilege:
            blocked = True
        finally:
            t2s_conn.rollback()
        _ok(blocked, "t2s_reader 不应能读取 app.users")
        writable = False
        try:
            t2s_conn.execute(
                "CREATE TABLE public._verify_should_fail (x int)"
            )
        except psycopg.Error:
            writable = True
        finally:
            t2s_conn.rollback()
        _ok(writable, "t2s_reader 不应能执行 DDL")
        return "t2s_reader 无法访问 app schema，也无法写入"
    report.run("t2s.role", "Text2SQL：数据库只读角色兜底", t2s_reader_blocked)

    def capture_paste():
        text = "我：验收用的临时问题\n助手：验收用的临时回答，导入后整体回滚。"
        result = import_service.parse_paste(text, title="验收速记", account_label="verify-share")
        _ok(result.ok, f"粘贴解析失败：{result.errors[:2]}")
        _ok([m["sender"] for m in result.conversations[0]["messages"]] == ["user", "assistant"],
            "粘贴解析未识别出用户/助手角色")
        outcome = import_service.ingest_plain_text(
            conn,
            user=alice_ctx,
            text=text,
            title="验收速记",
            account_label="verify-share",
        )
        _ok(outcome["conversations_new"] == 1, "粘贴收录应新增 1 个会话")
        _ok(outcome["messages_new"] == 2, "粘贴收录应新增 2 条消息")
        state["captured_id"] = outcome["conversation_id"]
        again = import_service.ingest_plain_text(
            conn,
            user=alice_ctx,
            text=text,
            title="验收速记",
            account_label="verify-share",
        )
        _ok(again["conversation_id"] == outcome["conversation_id"], "重复收录应复用同一会话")
        _ok(again["messages_new"] == 0 and again["messages_skipped"] == 2,
            "重复收录应跳过 2 条相同消息")
        return "粘贴识别 2 条角色消息，重复收录幂等"
    report.run("capture.paste", "采集：粘贴解析、收录与幂等", capture_paste)

    def token_ingest():
        created = token_service.create_token(conn, alice["id"], "验收脚本")
        stored = conn.execute(
            "SELECT token_hash, token_prefix FROM app.api_tokens WHERE id = %s",
            (created["id"],),
        ).fetchone()
        digest = hashlib.sha256(created["token"].encode("utf-8")).digest()
        _ok(bytes(stored["token_hash"]) == digest, "数据库应只保存令牌的 SHA-256 哈希")
        _ok(created["token"].startswith(stored["token_prefix"]), "令牌前缀与明文不一致")
        info = token_service.authenticate(conn, created["token"])
        _ok(info["user_id"] == alice["id"], "Bearer 认证未解析到正确用户")

        document = {
            "platform": "qoder",
            "account_label": "verify-token",
            "conversations": [
                {
                    "external_id": "verify-ingest-1",
                    "title": "验收采集",
                    "course_code": "DB",
                    "started_at": "2026-03-01T09:00:00+08:00",
                    "tags": ["验收"],
                    "messages": [
                        {"role": "user", "content": "采集令牌写入的问题"},
                        {"role": "assistant", "content": "采集令牌写入的回答"},
                    ],
                }
            ],
        }
        first = import_service.ingest_json(
            conn, user=alice_ctx, document=document
        )
        _ok(first["conversations_new"] == 1 and first["messages_new"] == 2,
            f"令牌采集应新增 1 会话 / 2 消息，实际 {first}")
        db_course = conn.execute(
            "SELECT id FROM app.courses WHERE code = 'DB'"
        ).fetchone()["id"]
        _ok(first["conversations"][0]["course_id"] == db_course,
            "course_code 未自动映射到 DB 课程")
        again = import_service.ingest_json(
            conn, user=alice_ctx, document=document
        )
        _ok(again["messages_new"] == 0 and again["messages_skipped"] == 2,
            "重复采集应只跳过相同消息")

        conn.execute(
            "UPDATE app.api_tokens SET window_started_at = now(), window_count = 60 "
            "WHERE id = %s",
            (created["id"],),
        )
        try:
            token_service.authenticate(conn, created["token"])
            raise AssertionError("超过每分钟 60 次时应限流")
        except RateLimited:
            pass
        conn.execute(
            "UPDATE app.api_tokens SET window_started_at = NULL, window_count = 0 "
            "WHERE id = %s",
            (created["id"],),
        )
        token_service.revoke_token(conn, created["id"], alice["id"])
        try:
            token_service.authenticate(conn, created["token"])
            raise AssertionError("已撤销令牌不应通过认证")
        except AuthRequired:
            pass
        return "哈希存储、课程映射、重复幂等、60 次/分钟限流与撤销均生效"
    report.run("token.ingest", "采集令牌：哈希存储、Bearer 认证、限流与幂等入库", token_ingest)

    def search_global():
        alice_hits = search_service.global_search(conn, alice_ctx, "验收速记")
        _ok(alice_hits["conversations"]["total"] == 1, "本人应能搜到刚收录的会话")
        bob_hits = search_service.global_search(conn, bob_ctx, "验收速记")
        _ok(bob_hits["conversations"]["total"] == 0, "未共享会话不应出现在他人搜索中")
        courses = search_service.global_search(conn, alice_ctx, "数据库")["courses"]
        _ok(any(row["code"] == "DB" for row in courses), "课程名搜索未命中 DB 课程")
        empty = search_service.global_search(conn, alice_ctx, "  ")
        _ok(empty["conversations"]["total"] == 0, "空关键词不应返回结果")
        return "全局搜索命中本人会话，未泄露他人数据"
    report.run("search.global", "搜索：全局检索与课程范围隔离", search_global)

    def inbox_bulk():
        conversation_id = state.get("captured_id")
        _ok(conversation_id is not None, "缺少粘贴收录的会话")
        db_course = conn.execute("SELECT id FROM app.courses WHERE code = 'DB'").fetchone()["id"]
        moved = conversation_service.bulk_organize_inbox(
            conn, [conversation_id], alice_ctx, course_id=db_course
        )
        _ok(moved == 1, f"收件箱批量归类应移动 1 个会话，实际 {moved}")
        row = conn.execute(
            "SELECT course_id FROM app.conversations WHERE id = %s",
            (conversation_id,),
        ).fetchone()
        _ok(row["course_id"] == db_course, "批量归类后课程未更新")
        return "未归类会话批量归入 DB 课程"
    report.run("inbox.organize", "收件箱：批量归入课程", inbox_bulk)

    def rollback_savepoint():
        before = _count(conn, "SELECT count(*) AS n FROM app.tags")
        try:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO app.tags (owner_id, name) VALUES (%s, 'verify-temp-tag')",
                    (alice["id"],),
                )
                raise RuntimeError("force rollback")
        except RuntimeError:
            pass
        after = _count(conn, "SELECT count(*) AS n FROM app.tags")
        _ok(before == after, "事务回滚后标签数量发生变化")
        return f"强制回滚后标签数保持 {before}"

    report.run("tx.rollback", "事务：异常回滚后计数不变", rollback_savepoint)

    def app_role_ddl():
        blocked = False
        try:
            conn.execute("CREATE TABLE app._verify_should_fail (x int)")
        except psycopg.errors.InsufficientPrivilege:
            blocked = True
        _ok(blocked, "ls_app 不应拥有 DDL 权限")
        return "应用角色 ls_app 无 DDL 权限"
    report.run("db.app_role", "数据库：应用角色最小权限（无 DDL）", app_role_ddl)

    return state


def main() -> int:
    parser = argparse.ArgumentParser(description="学习空间端到端验收")
    parser.add_argument(
        "--json",
        default=str(ROOT / "data" / "verification" / "report.json"),
        help="JSON 报告输出路径",
    )
    args = parser.parse_args()

    started = time.perf_counter()
    database = settings.database_url.rsplit("/", 1)[-1]
    print(f"验收目标数据库：{database}（所有写入检查将在结束时回滚）")
    print("-" * 72)

    try:
        conn = psycopg.connect(settings.database_url, autocommit=True, row_factory=dict_row)
        t2s_conn = psycopg.connect(
            settings.t2s_database_url, autocommit=False, row_factory=dict_row
        )
    except psycopg.OperationalError as exc:
        print(f"[FAIL] 无法连接数据库：{exc}", file=sys.stderr)
        return 2

    report = Report()
    before = _snapshot(conn)
    try:
        with conn.transaction():
            build_checks(conn, t2s_conn, report)
            raise Rollback
    except Rollback:
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 验收过程中断：{exc}", file=sys.stderr)
        report.checks.append(
            {"key": "harness", "label": "验收脚本执行", "status": "FAIL",
             "detail": f"{type(exc).__name__}: {exc}", "elapsed_ms": 0}
        )
    finally:
        after = _snapshot(conn)
        t2s_conn.close()

        def outer_rollback():
            _ok(before == after, f"整体回滚后计数变化：{before} -> {after}")
            return "写入检查整体回滚，演示库计数未变化"

        report.run("tx.outer_rollback", "事务：验收写入整体回滚", outer_rollback)
        conn.close()

    total_ms = int((time.perf_counter() - started) * 1000)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "database": database,
        "summary": {
            "total": len(report.checks),
            "passed": report.passed,
            "failed": report.failed,
            "duration_ms": total_ms,
        },
        "counts": before,
        "checks": report.checks,
    }
    output = Path(args.json)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("-" * 72)
    print(
        f"结果：{report.passed}/{len(report.checks)} PASS，{report.failed} FAIL，"
        f"耗时 {total_ms} ms"
    )
    print(f"报告：{output}")
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
