from __future__ import annotations

import contextlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "learning-space", "version": "0.2.0"}
REQUEST_TIMEOUT = 20
MAX_TOOL_TEXT = 20_000
ANNOTATION_KINDS = ("note", "question", "correction", "summary")

TOOLS = [
    {
        "name": "search",
        "description": "在当前学习空间内检索会话、批注、课程和标签，返回摘要与命中片段。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "关键词，例如 隔离级别"},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_conversation",
        "description": "读取一个会话的完整消息与批注；只能读取当前令牌用户有权访问的会话。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "integer", "minimum": 1},
            },
            "required": ["conversation_id"],
        },
    },
    {
        "name": "list_due_reviews",
        "description": "列出今天到期或已逾期的复习项，含批注与消息片段。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 20,
                },
            },
        },
    },
    {
        "name": "add_annotation",
        "description": "给一条消息新增批注。需要令牌显式授予 annotate 作用域，不能修改或删除已有批注。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "integer", "minimum": 1},
                "kind": {
                    "type": "string",
                    "enum": list(ANNOTATION_KINDS),
                    "default": "note",
                },
                "content": {"type": "string", "minLength": 1, "maxLength": 2000},
                "event_id": {
                    "type": "string",
                    "description": "可选幂等键，重试同一批注不会重复写入",
                },
            },
            "required": ["message_id", "content"],
        },
    },
]
READ_TOOLS = {"search", "get_conversation", "list_due_reviews"}


class ToolError(Exception):
    """HTTP 调用或输入校验失败，作为工具结果返回给模型。"""


class McpError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class HttpTransport:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path, data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = None
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("detail")
            except (ValueError, AttributeError):
                detail = None
            raise ToolError(detail or f"学习空间返回 HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ToolError(f"无法连接学习空间服务：{exc.reason}") from exc
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ToolError("学习空间返回了非 JSON 响应") from exc
        if not isinstance(data, dict):
            raise ToolError("学习空间返回了意外的数据结构")
        return data


def _clip(text, limit: int = 240) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _positive_int(value, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"{name} 必须是正整数") from exc
    if number < 1:
        raise ToolError(f"{name} 必须是正整数")
    return number


def _bounded(value, default: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(1, min(number, maximum))


def format_search(data: dict[str, Any], limit: int) -> str:
    mode = str((data.get("retrieval") or {}).get("mode") or "")
    mode_label = {"hybrid": "混合检索", "lexical": "词法检索"}.get(mode, mode or "检索")
    rows = data.get("conversations") or []
    lines = [
        f"检索「{data.get('query', '')}」：会话 {data.get('total', len(rows))} 条，"
        f"批注 {len(data.get('annotations') or [])} 条，{mode_label}"
    ]
    for row in rows[:limit]:
        course = row.get("course_code") or "未归类"
        lines.append(
            f"- [#{row['id']}] [{course}] {row['title']}（{row['message_count']} 条消息）"
        )
        if row.get("snippet"):
            lines.append(f"  {_clip(row['snippet'])}")
    if len(rows) > limit:
        lines.append(f"（仅显示前 {limit} 条会话）")
    annotations = data.get("annotations") or []
    if annotations:
        lines.append("批注：")
        for row in annotations[:10]:
            lines.append(
                f"- #{row['id']} [{row['kind']}] 会话「{row['conversation_title']}」："
                f"{_clip(row.get('snippet') or row['content'])}"
            )
    courses = data.get("courses") or []
    if courses:
        lines.append("课程：" + "；".join(f"{c['code']} {c['name']}" for c in courses))
    tags = data.get("tags") or []
    if tags:
        lines.append("标签：" + "、".join(tag["name"] for tag in tags))
    return "\n".join(lines)


def format_conversation(data: dict) -> str:
    conversation = data["conversation"]
    counts = data["counts"]
    info = conversation.get("platform") or "未知平台"
    if conversation.get("course_code"):
        info = f"{conversation['course_code']} {conversation.get('course_name') or ''} · {info}"
    tags = data.get("tags") or []
    lines = [
        f"# {conversation['title']}",
        f"会话 #{conversation['id']} · {info} · 消息 {counts['total']} 条"
        f"（用户 {counts['user_count']} / 助手 {counts['assistant_count']}）",
    ]
    if tags:
        lines.append("标签：" + "、".join(tag["name"] for tag in tags))
    lines.append("")
    for message in data.get("messages") or []:
        stamp = message.get("created_at")
        stamp_text = str(stamp)[:16] if stamp else ""
        lines.append(
            f"[{message['seq_no']}] {message['sender']} {stamp_text} · 消息 #{message['id']}"
        )
        lines.append(message["content"])
        for annotation in message.get("annotations") or []:
            lines.append(
                f"  批注 #{annotation['id']} {annotation['kind']}/{annotation['status']} "
                f"{annotation.get('author') or ''}：{annotation['content']}"
            )
        lines.append("")
    text = "\n".join(lines).rstrip()
    if len(text) > MAX_TOOL_TEXT:
        text = text[:MAX_TOOL_TEXT] + "\n…（内容过长，已截断）"
    return text


def format_reviews(data: dict) -> str:
    items = data.get("items") or []
    lines = [f"今日到期复习 {data.get('total', len(items))} 项（本次返回 {len(items)} 项）"]
    for item in items:
        course = item.get("course_code") or "未归类"
        overdue = f" · 逾期 {item['due_date']} 到期" if item.get("is_overdue") else ""
        lines.append(
            f"- 复习 #{item['id']} [{course}] 会话「{item['conversation_title']}」"
            f" 消息 #{item['message_id']} · 间隔 {item['interval_days']} 天"
            f" · 已复习 {item['review_count']} 次{overdue}"
        )
        lines.append(
            f"  批注 [{item['annotation_kind']}] {_clip(item['annotation_content'], 160)}"
        )
        if item.get("snippet"):
            lines.append(f"  片段：{_clip(item['snippet'], 160)}")
    if not items:
        lines.append("今天没有到期项，可以先整理新批注或休息一下。")
    return "\n".join(lines)


class McpServer:
    def __init__(self, transport):
        self.transport = transport
        self._scopes: set[str] | None = None

    def scopes(self) -> set[str]:
        if self._scopes is None:
            try:
                data = self.transport.request("GET", "/api/mcp/me")
                self._scopes = set(data.get("scopes") or ())
            except ToolError:
                self._scopes = set()
        return self._scopes

    def visible_tools(self) -> list[dict]:
        scopes = self.scopes()
        return [
            tool
            for tool in TOOLS
            if tool["name"] in READ_TOOLS or "annotate" in scopes
        ]

    def handle(self, message):
        if isinstance(message, list):
            if not message:
                return self._error(None, -32600, "无效的 JSON-RPC 请求")
            responses = [self._handle_one(item) for item in message]
            responses = [item for item in responses if item is not None]
            return responses or None
        return self._handle_one(message)

    def _handle_one(self, message):
        if not isinstance(message, dict) or not isinstance(message.get("method"), str):
            return self._error(None, -32600, "无效的 JSON-RPC 请求")
        has_id = "id" in message
        message_id = message.get("id")
        method = message["method"]
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return self._error(message_id, -32602, "params 必须是对象")

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                    "instructions": "读取当前学习空间的会话、批注和复习队列；写入批注需令牌 annotate 作用域。",
                }
            elif method.startswith("notifications/"):
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.visible_tools()}
            elif method == "tools/call":
                result = self._call_tool(params)
            else:
                if not has_id:
                    return None
                return self._error(message_id, -32601, f"未知方法：{method}")
        except McpError as exc:
            return self._error(message_id, exc.code, exc.message)

        if not has_id:
            return None
        return {"jsonrpc": "2.0", "id": message_id, "result": result}

    def _call_tool(self, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not name:
            raise McpError(-32602, "缺少工具名称")
        if not isinstance(arguments, dict):
            raise McpError(-32602, "arguments 必须是对象")
        available = {tool["name"] for tool in self.visible_tools()}
        if name not in available:
            raise McpError(-32602, f"未知工具或令牌无权使用：{name}")
        try:
            if name == "search":
                text = self._search(arguments)
            elif name == "get_conversation":
                text = self._get_conversation(arguments)
            elif name == "list_due_reviews":
                text = self._due_reviews(arguments)
            else:
                text = self._add_annotation(arguments)
        except ToolError as exc:
            return {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            }
        return {"content": [{"type": "text", "text": text}], "isError": False}

    def _search(self, arguments: dict) -> str:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ToolError("query 不能为空")
        limit = _bounded(arguments.get("limit"), 10, 50)
        params = urllib.parse.urlencode({"q": query, "limit": limit})
        data = self.transport.request("GET", f"/api/mcp/search?{params}")
        return format_search(data, limit)

    def _get_conversation(self, arguments: dict) -> str:
        conversation_id = _positive_int(arguments.get("conversation_id"), "conversation_id")
        data = self.transport.request(
            "GET", f"/api/mcp/conversations/{conversation_id}"
        )
        return format_conversation(data)

    def _due_reviews(self, arguments: dict) -> str:
        limit = _bounded(arguments.get("limit"), 20, 100)
        data = self.transport.request("GET", f"/api/mcp/reviews/due?limit={limit}")
        return format_reviews(data)

    def _add_annotation(self, arguments: dict) -> str:
        message_id = _positive_int(arguments.get("message_id"), "message_id")
        content = str(arguments.get("content") or "").strip()
        if not content:
            raise ToolError("content 不能为空")
        kind = arguments.get("kind") or "note"
        if kind not in ANNOTATION_KINDS:
            raise ToolError("kind 必须是 note / question / correction / summary")
        payload = {"message_id": message_id, "kind": kind, "content": content}
        if arguments.get("event_id"):
            payload["event_id"] = str(arguments["event_id"])
        data = self.transport.request("POST", "/api/mcp/annotations", payload)
        annotation = data.get("annotation") or {}
        if annotation.get("duplicate"):
            return f"批注 #{annotation.get('id')} 已存在，幂等重放未重复写入。"
        return f"已创建批注 #{annotation.get('id')}（{annotation.get('kind')}）。"

    @staticmethod
    def _error(message_id, code: int, message: str) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "error": {"code": code, "message": message},
        }


def handle_line(server: McpServer, line: str) -> str | None:
    line = line.strip()
    if not line:
        return None
    try:
        message = json.loads(line)
    except ValueError:
        return json.dumps(
            McpServer._error(None, -32700, "JSON 解析失败"), ensure_ascii=False
        )
    response = server.handle(message)
    if response is None:
        return None
    return json.dumps(response, ensure_ascii=False)


def main(argv=None) -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            with contextlib.suppress(ValueError):
                reconfigure(encoding="utf-8")
    token = (os.environ.get("LS_TOKEN") or "").strip()
    if not token:
        print("缺少 LS_TOKEN 环境变量", file=sys.stderr)
        return 2
    base_url = (os.environ.get("LS_BASE_URL") or "http://127.0.0.1:8000").strip()
    server = McpServer(HttpTransport(base_url, token))
    for line in sys.stdin:
        output = handle_line(server, line)
        if output is not None:
            print(output, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
