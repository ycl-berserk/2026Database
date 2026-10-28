"""命令行客户端：用采集令牌调用只读搜索与导入接口。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib import error, parse, request

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
TIMEOUT_SECONDS = 30


class CliError(Exception):
    pass


def _normalize_base_url(value: str | None) -> str:
    return (value or DEFAULT_BASE_URL).rstrip("/")


def _resolve_token(args: argparse.Namespace) -> str:
    token = (args.token or os.environ.get("LS_TOKEN") or "").strip()
    if not token:
        raise CliError("缺少采集令牌：使用 --token 或环境变量 LS_TOKEN")
    return token


def _request_json(
    base_url: str,
    path: str,
    token: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
) -> dict:
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(base_url + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(raw).get("detail") or raw
        except json.JSONDecodeError:
            detail = raw
        raise CliError(f"服务端返回 {exc.code}：{detail}") from exc
    except error.URLError as exc:
        raise CliError(f"无法连接 {base_url}：{exc.reason}") from exc
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise CliError("服务端返回的不是有效 JSON") from exc
    if not isinstance(parsed, dict):
        raise CliError("服务端返回的 JSON 结构不符合预期")
    return parsed


def prepare_document(
    document: object,
    *,
    course_code: str | None = None,
    platform: str | None = None,
) -> dict:
    if not isinstance(document, dict):
        raise CliError("导入文件必须是包含 platform 与 conversations 的 JSON 对象")
    prepared = dict(document)
    if platform:
        prepared["platform"] = platform
    if course_code:
        conversations = prepared.get("conversations")
        if not isinstance(conversations, list):
            raise CliError("导入文件缺少 conversations 数组")
        prepared["conversations"] = [
            {**conversation, "course_code": course_code}
            if isinstance(conversation, dict)
            else conversation
            for conversation in conversations
        ]
    return prepared


def _retrieval_label(retrieval: dict | None) -> str:
    return "混合检索" if (retrieval or {}).get("mode") == "hybrid" else "词法检索"


def _one_line(text: str | None, limit: int = 90) -> str:
    compact = " ".join((text or "").split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def format_search(data: dict, limit: int = 5) -> str:
    conversations = data.get("conversations") or {}
    rows = conversations.get("rows") or []
    total = conversations.get("total") or 0
    mode = _retrieval_label(conversations.get("retrieval"))
    lines = [f"查询：{data.get('q') or ''}  ·  {mode}  ·  {total} 个会话"]

    shown = rows[: max(1, limit)]
    if not shown:
        lines.append("（没有匹配的会话）")
    for row in shown:
        course = row.get("course_code") or "未归类"
        lines.append(
            f"- [{course}] {row.get('title') or '未命名会话'}"
            f"（{row.get('message_count') or 0} 条消息）  #{row.get('id')}"
        )
        snippet = _one_line(row.get("snippet"))
        if snippet:
            lines.append(f"  {snippet}")
    if total > len(shown):
        lines.append(f"  仅显示前 {len(shown)} 条会话")

    annotations = data.get("annotations") or []
    if annotations:
        lines.append(f"批注 {len(annotations)} 条：")
        for row in annotations[:limit]:
            lines.append(
                f"- [{row.get('kind')}] {_one_line(row.get('snippet') or row.get('content'))}"
                f"  #{row.get('id')}"
            )

    courses = data.get("courses") or []
    if courses:
        lines.append("课程：" + "、".join(f"{row.get('code')} {row.get('name')}" for row in courses[:limit]))

    tags = data.get("tags") or []
    if tags:
        lines.append("标签：" + "、".join(str(row.get("name")) for row in tags[:limit]))
    return "\n".join(lines)


def format_import(result: dict) -> str:
    lines = [
        f"导入完成：批次 #{result.get('batch_id')}",
        "新增会话 {conversations_new}，更新 {conversations_updated}；"
        "新增消息 {messages_new}，跳过 {messages_skipped}，冲突 {conflicts}；"
        "新标签 {tags_new}".format(
            conversations_new=result.get("conversations_new", 0),
            conversations_updated=result.get("conversations_updated", 0),
            messages_new=result.get("messages_new", 0),
            messages_skipped=result.get("messages_skipped", 0),
            conflicts=result.get("conflicts", 0),
            tags_new=result.get("tags_new", 0),
        ),
    ]
    for row in result.get("conversations") or []:
        course = f"课程 #{row['course_id']}" if row.get("course_id") else "未归类"
        lines.append(f"- #{row.get('id')} {row.get('title') or row.get('external_id')}（{course}）")
    warnings = result.get("warnings") or []
    if warnings:
        lines.append(f"警告 {len(warnings)} 条：")
        lines.extend(f"- {_one_line(item, 120)}" for item in warnings)
    return "\n".join(lines)


def command_search(args: argparse.Namespace) -> int:
    token = _resolve_token(args)
    base_url = _normalize_base_url(args.base_url)
    query = parse.urlencode({"q": args.query.strip()})
    data = _request_json(base_url, f"/api/search?{query}", token)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(format_search(data, limit=args.limit))
    return 0


def command_import(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.is_file():
        raise CliError(f"找不到导入文件：{path}")
    if path.suffix.lower() in {".md", ".markdown"}:
        raise CliError("命令行导入当前支持统一 JSON；Markdown 请在网站导入页上传")
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise CliError(f"读取导入文件失败：{exc}") from exc
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CliError(
            f"JSON 解析失败：第 {exc.lineno} 行第 {exc.colno} 列，{exc.msg}"
        ) from exc

    prepared = prepare_document(
        document, course_code=args.course_code, platform=args.platform
    )
    token = _resolve_token(args)
    base_url = _normalize_base_url(args.base_url)
    result = _request_json(
        base_url, "/api/ingest", token, method="POST", payload=prepared
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_import(result))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ls",
        description="个人学习空间命令行：用采集令牌搜索与导入聊天记录",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("LS_BASE_URL", DEFAULT_BASE_URL),
        help=f"网站地址，默认读取 LS_BASE_URL 或 {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="采集令牌；默认读取环境变量 LS_TOKEN",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="搜索会话、批注、课程与标签")
    search.add_argument("query", help="搜索关键词")
    search.add_argument("--limit", type=int, default=5, help="每类最多显示几条，默认 5")
    search.add_argument("--json", action="store_true", help="输出原始 JSON")
    search.set_defaults(func=command_search)

    importer = subparsers.add_parser("import", help="用统一 JSON 文件导入会话")
    importer.add_argument("file", help="统一 JSON 导出文件")
    importer.add_argument("--course-code", help="覆盖所有会话的课程编号")
    importer.add_argument(
        "--platform",
        choices=("qoder", "chatgpt", "deepseek", "claude", "other"),
        help="覆盖来源平台",
    )
    importer.add_argument("--json", action="store_true", help="输出原始 JSON")
    importer.set_defaults(func=command_import)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
