from __future__ import annotations

import json

from app import mcp_server
from app.mcp_server import McpServer, ToolError, handle_line


class FakeTransport:
    def __init__(self, scopes=("read", "ingest"), responses=None, error=None):
        self.scopes = scopes
        self.responses = responses or {}
        self.error = error
        self.calls = []

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if self.error:
            raise ToolError(self.error)
        if path == "/api/mcp/me":
            return {"user_id": 1, "display_name": "林晓", "scopes": list(self.scopes)}
        for prefix, data in self.responses.items():
            if path.startswith(prefix):
                return data
        raise AssertionError(f"unexpected request: {method} {path}")


def _server(scopes=("read", "ingest"), responses=None, error=None):
    transport = FakeTransport(scopes=scopes, responses=responses, error=error)
    return McpServer(transport), transport


def test_initialize_reports_protocol_and_capabilities():
    server, _ = _server()
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response["result"]["protocolVersion"] == mcp_server.PROTOCOL_VERSION
    assert response["result"]["capabilities"]["tools"]["listChanged"] is False
    assert response["result"]["serverInfo"]["name"] == "learning-space"


def test_tools_list_hides_write_tool_without_annotate_scope():
    server, _ = _server()
    names = {tool["name"] for tool in server.visible_tools()}
    assert names == {"search", "get_conversation", "list_due_reviews"}

    writer, _ = _server(scopes=("read", "ingest", "annotate"))
    names = {tool["name"] for tool in writer.visible_tools()}
    assert "add_annotation" in names


def test_tools_list_reflects_authenticated_scopes():
    server, transport = _server(scopes=("read",))
    response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {tool["name"] for tool in response["result"]["tools"]}
    assert "add_annotation" not in names
    assert any(path == "/api/mcp/me" for _, path, _ in transport.calls)


def test_search_tool_formats_results():
    data = {
        "query": "隔离级别",
        "retrieval": {"mode": "lexical"},
        "total": 1,
        "conversations": [
            {
                "id": 7,
                "title": "事务与隔离级别",
                "course_code": "DB",
                "message_count": 4,
                "snippet": "SQL 标准定义了四种隔离级别。",
            }
        ],
        "annotations": [
            {
                "id": 3,
                "kind": "note",
                "content": "隔离级别要对比记忆。",
                "conversation_title": "事务与隔离级别",
            }
        ],
        "courses": [{"code": "DB", "name": "数据库系统概论"}],
        "tags": [{"name": "事务"}],
    }
    server, transport = _server(responses={"/api/mcp/search": data})
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "search", "arguments": {"query": "隔离级别", "limit": 5}},
        }
    )
    text = response["result"]["content"][0]["text"]
    assert response["result"]["isError"] is False
    assert "词法检索" in text
    assert "[#7] [DB] 事务与隔离级别" in text
    assert "隔离级别要对比记忆" in text
    method, path, _ = transport.calls[-1]
    assert method == "GET"
    assert "q=%E9%9A%94%E7%A6%BB%E7%BA%A7%E5%88%AB" in path
    assert "limit=5" in path


def test_get_conversation_formats_messages_and_annotations():
    data = {
        "conversation": {
            "id": 7,
            "title": "事务与隔离级别",
            "platform": "qoder",
            "course_code": "DB",
            "course_name": "数据库系统概论",
        },
        "counts": {"total": 1, "user_count": 1, "assistant_count": 0},
        "tags": [{"name": "事务"}],
        "messages": [
            {
                "id": 11,
                "seq_no": 1,
                "sender": "user",
                "content": "可重复读能避免幻读吗？",
                "created_at": "2026-02-01T10:01:00+08:00",
                "annotations": [
                    {
                        "id": 4,
                        "kind": "question",
                        "status": "open",
                        "content": "需要查 PostgreSQL 文档",
                        "author": "林晓",
                    }
                ],
            }
        ],
    }
    server, _ = _server(responses={"/api/mcp/conversations/7": data})
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "get_conversation", "arguments": {"conversation_id": 7}},
        }
    )
    text = response["result"]["content"][0]["text"]
    assert "# 事务与隔离级别" in text
    assert "[1] user" in text
    assert "批注 #4 question/open 林晓" in text


def test_due_reviews_formats_overdue_items():
    data = {
        "total": 1,
        "items": [
            {
                "id": 5,
                "annotation_id": 3,
                "annotation_kind": "note",
                "annotation_content": "隔离级别要对比记忆。",
                "message_id": 12,
                "seq_no": 2,
                "conversation_id": 7,
                "conversation_title": "事务与隔离级别",
                "course_code": "DB",
                "due_date": "2026-02-01",
                "interval_days": 1,
                "review_count": 2,
                "is_overdue": True,
                "snippet": "可重复读依赖快照隔离。",
            }
        ],
    }
    server, _ = _server(responses={"/api/mcp/reviews/due": data})
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "list_due_reviews", "arguments": {}},
        }
    )
    text = response["result"]["content"][0]["text"]
    assert "今日到期复习 1 项" in text
    assert "逾期 2026-02-01" in text
    assert "间隔 1 天" in text


def test_add_annotation_requires_annotate_scope_and_posts_payload():
    reader, transport = _server(scopes=("read", "ingest"))
    denied = reader.handle(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "add_annotation",
                "arguments": {"message_id": 11, "content": "对比四种隔离级别"},
            },
        }
    )
    assert denied["error"]["code"] == -32602
    assert not any(method == "POST" for method, _, _ in transport.calls)

    data = {"annotation": {"id": 9, "kind": "note", "duplicate": False}}
    writer, transport = _server(
        scopes=("read", "ingest", "annotate"),
        responses={"/api/mcp/annotations": data},
    )
    created = writer.handle(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "add_annotation",
                "arguments": {
                    "message_id": 11,
                    "kind": "note",
                    "content": "对比四种隔离级别",
                },
            },
        }
    )
    assert created["result"]["isError"] is False
    assert "已创建批注 #9" in created["result"]["content"][0]["text"]
    method, path, payload = transport.calls[-1]
    assert (method, path) == ("POST", "/api/mcp/annotations")
    assert payload["message_id"] == 11
    assert payload["content"] == "对比四种隔离级别"


def test_tool_http_errors_become_is_error_results():
    server, _ = _server(error="当前令牌没有读取权限")
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {"name": "search", "arguments": {"query": "事务"}},
        }
    )
    assert response["result"]["isError"] is True
    assert "没有读取权限" in response["result"]["content"][0]["text"]


def test_notifications_and_unknown_methods():
    server, _ = _server()
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    response = server.handle({"jsonrpc": "2.0", "id": 9, "method": "unknown/method"})
    assert response["error"]["code"] == -32601


def test_handle_line_parses_errors_and_batches():
    server, _ = _server()
    assert json.loads(handle_line(server, "{not json"))["error"]["code"] == -32700

    batch = [
        {"jsonrpc": "2.0", "id": 10, "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]
    responses = json.loads(handle_line(server, json.dumps(batch)))
    assert len(responses) == 1
    assert responses[0]["id"] == 10
    assert responses[0]["result"] == {}
