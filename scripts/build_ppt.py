from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "docs" / "screenshots"
OUT = ROOT / "docs" / "汇报.pptx"

PAPER = RGBColor(0xF7, 0xF4, 0xEB)
INK = RGBColor(0x1F, 0x2A, 0x24)
MUTED = RGBColor(0x6C, 0x75, 0x6F)
GREEN = RGBColor(0x2F, 0x5D, 0x4E)
GREEN_DARK = RGBColor(0x22, 0x43, 0x39)
RUST = RGBColor(0xB4, 0x69, 0x3F)
LINE = RGBColor(0xD8, 0xD1, 0xC0)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
SOFT = RGBColor(0xEC, 0xE7, 0xDA)
CREAM = RGBColor(0xD9, 0xC8, 0xA8)
PALE = RGBColor(0xE8, 0xE3, 0xD3)

FONT = "Microsoft YaHei"
MONO = "Consolas"

W, H = 13.333, 7.5


def solid(shape, color: RGBColor, line: RGBColor | None = None, width=1.0):
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line
        shape.line.width = Pt(width)
    return shape


def rect(slide, x, y, w, h, color, radius=False, line=None, width=1.0):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(shape_type, Inches(x), Inches(y), Inches(w), Inches(h))
    solid(shape, color, line, width)
    if radius:
        shape.adjustments[0] = 0.06
    return shape


def text(
    slide,
    x,
    y,
    w,
    h,
    value,
    *,
    size=16,
    bold=False,
    color=INK,
    font=FONT,
    align=PP_ALIGN.LEFT,
    anchor=MSO_ANCHOR.TOP,
    spacing=1.15,
    margins=(0.04, 0.03, 0.04, 0.03),
):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = anchor
    frame.margin_left = Inches(margins[0])
    frame.margin_top = Inches(margins[1])
    frame.margin_right = Inches(margins[2])
    frame.margin_bottom = Inches(margins[3])
    for index, line in enumerate(value.split("\n")):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.alignment = align
        paragraph.line_spacing = spacing
        run = paragraph.add_run()
        run.text = line
        run.font.name = font
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = color
    return box


def bullets(slide, x, y, w, h, items, *, size=14, color=INK, gap=8, bullet_color=GREEN):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.word_wrap = True
    frame.margin_left = 0
    frame.margin_right = 0
    frame.margin_top = 0
    frame.margin_bottom = 0
    for index, item in enumerate(items):
        level = 0
        if isinstance(item, tuple):
            item, level = item
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.level = level
        paragraph.line_spacing = 1.18
        paragraph.space_after = Pt(gap)
        run = paragraph.add_run()
        run.text = ("• " if level == 0 else "– ") + item
        run.font.name = FONT
        run.font.size = Pt(size - (2 if level else 0))
        run.font.color.rgb = bullet_color if level == 0 else MUTED
    return box


def chip(slide, x, y, w, label, *, fill=SOFT, color=GREEN, size=11):
    rect(slide, x, y, w, 0.32, fill, radius=True)
    text(slide, x, y + 0.015, w, 0.29, label, size=size, color=color,
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)


def header(slide, kicker, title, page):
    rect(slide, 0, 0, W, H, PAPER)
    text(slide, 0.72, 0.42, 9.8, 0.28, kicker, size=11, bold=True, color=RUST)
    text(slide, 0.72, 0.70, 11.9, 0.62, title, size=26, bold=True, color=INK)
    rect(slide, 0.72, 1.36, 11.9, 0.018, LINE)
    text(slide, 11.65, 7.06, 0.9, 0.25, f"{page:02d}", size=10, color=MUTED,
         align=PP_ALIGN.RIGHT)
    text(slide, 0.72, 7.06, 8.0, 0.25,
         "个人学习空间 · 《数据库系统概论》阶段性汇报", size=9, color=MUTED)


def notes(slide, value):
    slide.notes_slide.notes_text_frame.text = value


def picture_fit(slide, filename, x, y, w, h, *, frame=True, crop=None):
    path = SHOTS / filename
    if not path.exists():
        raise FileNotFoundError(path)
    source: str | io.BytesIO = str(path)
    with Image.open(path) as image:
        image.load()
        if crop is None:
            iw, ih = image.size
        else:
            left, top, right, bottom = crop
            iw, ih = image.size
            cropped = image.crop((int(iw * left), int(ih * top), int(iw * right), int(ih * bottom)))
            iw, ih = cropped.size
            stream = io.BytesIO()
            cropped.save(stream, format="PNG")
            stream.seek(0)
            source = stream
        ratio = min(w / iw, h / ih)
        pw, ph = iw * ratio, ih * ratio
        px, py = x + (w - pw) / 2, y + (h - ph) / 2
        if frame:
            rect(slide, px - 0.05, py - 0.05, pw + 0.1, ph + 0.1, WHITE, radius=True,
                 line=LINE, width=0.8)
        slide.shapes.add_picture(source, Inches(px), Inches(py), Inches(pw), Inches(ph))
    return px, py, pw, ph


def code_block(slide, x, y, w, h, code, *, size=10, fill=GREEN_DARK, color=PALE):
    rect(slide, x, y, w, h, fill, radius=True)
    text(slide, x + 0.16, y + 0.11, w - 0.32, h - 0.22, code, size=size,
         color=color, font=MONO, spacing=1.1)


def table(slide, x, y, w, h, rows, widths, *, font_size=10.5, header_size=10.5):
    shape = slide.shapes.add_table(len(rows), len(rows[0]), Inches(x), Inches(y), Inches(w), Inches(h))
    tbl = shape.table
    for column, width in enumerate(widths):
        tbl.columns[column].width = Inches(width)
    for row_index, row in enumerate(rows):
        tbl.rows[row_index].height = Inches(h / len(rows))
        for column, value in enumerate(row):
            cell = tbl.cell(row_index, column)
            cell.margin_left = Inches(0.08)
            cell.margin_right = Inches(0.06)
            cell.margin_top = Inches(0.03)
            cell.margin_bottom = Inches(0.03)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.fill.solid()
            cell.fill.fore_color.rgb = GREEN if row_index == 0 else (
                WHITE if row_index % 2 else SOFT
            )
            frame = cell.text_frame
            frame.word_wrap = True
            paragraph = frame.paragraphs[0]
            paragraph.alignment = PP_ALIGN.LEFT
            run = paragraph.add_run()
            run.text = str(value)
            run.font.name = FONT
            run.font.size = Pt(header_size if row_index == 0 else font_size)
            run.font.bold = row_index == 0
            run.font.color.rgb = WHITE if row_index == 0 else INK
    return tbl


def card(slide, x, y, w, h, title, body, *, number=None, accent=GREEN, title_size=15, body_size=11.5):
    rect(slide, x, y, w, h, WHITE, radius=True, line=LINE, width=0.7)
    rect(slide, x, y, 0.08, h, accent, radius=False)
    top = y + 0.16
    if number is not None:
        text(slide, x + 0.24, top, 0.7, 0.42, number, size=24, bold=True, color=accent)
        text(slide, x + 0.92, top + 0.02, w - 1.1, 0.32, title, size=title_size, bold=True, color=INK)
        text(slide, x + 0.24, top + 0.5, w - 0.44, h - 0.66, body, size=body_size,
             color=MUTED, spacing=1.12)
    else:
        text(slide, x + 0.24, top, w - 0.44, 0.32, title, size=title_size, bold=True, color=accent)
        text(slide, x + 0.24, top + 0.4, w - 0.44, h - 0.56, body, size=body_size,
             color=MUTED, spacing=1.14)


def title_slide(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    rect(slide, 0, 0, W, H, GREEN_DARK)
    rect(slide, 8.9, -1.2, 6.2, 6.2, RGBColor(0x2B, 0x50, 0x44), radius=True)
    rect(slide, 10.6, 3.4, 4.6, 4.6, RGBColor(0x37, 0x5E, 0x50), radius=True)
    text(slide, 0.85, 0.82, 8.4, 0.3, "数据库系统概论 · 阶段性汇报（8 分钟）", size=12,
         bold=True, color=CREAM)
    text(slide, 0.85, 1.55, 8.6, 1.25, "个人学习空间", size=46, bold=True, color=WHITE)
    text(slide, 0.85, 2.85, 7.8, 1.0,
         "把与大模型的聊天记录\n变成可查询、可批注、可复习的课程数据",
         size=21, color=PALE, spacing=1.22)
    chip(slide, 0.85, 4.55, 1.85, "PostgreSQL 16")
    chip(slide, 2.82, 4.55, 1.95, "17 表 · 15 视图")
    chip(slide, 4.89, 4.55, 1.45, "模拟数据")
    text(slide, 0.85, 6.35, 8.2, 0.32,
         "选题 2 · 汇报人：____________ · 2026-11-10", size=12,
         color=RGBColor(0xC9, 0xD3, 0xCB))
    notes(slide, "0:15 开场：这是数据库课设，今天只讲数据库——模型、SQL、索引、权限、事务与触发器；"
                 "每条结论都能用 sql/showcase.sql 现场复现。")


def slide_model(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    header(slide, "01 / 概念模型 · 0:45", "8 个核心实体：聊天记录被拆成可查询的关系", 2)

    bullets(slide, 0.72, 1.62, 5.35, 4.3, [
        "用户、课程、来源、会话、消息、标签、批注、复习项",
        "用户 M:N 课程，桥表带角色 owner / editor / viewer",
        "会话 M:N 标签；批注与复习项作为弱实体依附消息",
        "用户 1:N 来源 → 会话 → 消息 → 批注",
        "同一批注可进入不同用户各自的复习队列",
        "来源 → 会话 → 消息是一条链，不重复存属主与课程",
    ], size=14, gap=11)

    rect(slide, 0.72, 5.72, 5.35, 0.92, SOFT, radius=True)
    text(slide, 0.92, 5.82, 4.98, 0.72,
         "删除策略：消息随会话 CASCADE；会话随课程置空；\n有会话的来源 RESTRICT。", size=11.5, color=GREEN)

    picture_fit(slide, "16-er-chen.png", 6.25, 1.5, 6.35, 5.35)
    notes(slide, "0:45 指 ER 图：先讲实体与联系，再讲三条删除策略为什么不同——"
                 "CASCADE 只用于弱实体，课程删除保留学习记录，来源有数据时禁止删。")


def slide_schema(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    header(slide, "02 / 逻辑设计 · 1:00", "17 张表：15 张业务表 + 审计表 + 引导状态表", 3)

    rows = [
        ["领域", "表", "关键约束"],
        ["账户与权限", "users · course_members", "系统角色；每课恰好一个 owner；复合主键"],
        ["内容", "sources · conversations · messages", "(source, external_id)、(conversation, seq_no) 唯一"],
        ["组织", "courses · tags · conversation_tags", "(created_by, code, semester) 唯一；标签同属主"],
        ["学习", "annotations · review_items · review_logs", "UNIQUE(user_id, annotation_id)；事件幂等"],
        ["知识结构", "tag_edges", "先修边唯一；触发器递归防环"],
        ["采集与运维", "import_batches · api_tokens · text2sql_log · user_onboarding", "预览 token 唯一；令牌只存哈希；引导状态按用户唯一"],
        ["审计", "audit_log", "只记对象/主键/变更列名；应用角色只读"],
    ]
    table(slide, 0.72, 1.58, 11.9, 3.52, rows, [1.55, 4.45, 5.9], font_size=9.8, header_size=10)

    text(slide, 0.72, 5.24, 6.0, 0.3, "范式取舍", size=12.5, bold=True, color=GREEN)
    bullets(slide, 0.72, 5.6, 6.0, 1.3, [
        "3NF：会话不冗余属主，消息不冗余课程 / 来源",
        "不存可派生计数，统计全部实时 count(*)",
        "受控冗余：payload JSONB 仅预览期，row_count 是审计快照",
    ], size=11.5, gap=4)

    code_block(slide, 7.05, 5.24, 5.57, 1.4,
               "-- 每门课最多一个 owner\n"
               "CREATE UNIQUE INDEX ... ON course_members(course_id)\n"
               "  WHERE role = 'owner';\n"
               "-- 事务结束时仍必须恰好一个 owner\n"
               "CREATE CONSTRAINT TRIGGER ... DEFERRABLE\n"
               "  INITIALLY DEFERRED ...", size=9.2)
    notes(slide, "1:00 按五组讲表；重点两句：没有冗余属主列，没有统计缓存列；"
                 "“恰好一个 owner”由部分唯一索引 + 延迟约束触发器共同保证。")


def slide_queries(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    header(slide, "03 / 关键 SQL · 1:15", "窗口、补零、递归：三条查询都有可执行结果", 4)

    code_block(slide, 0.72, 1.58, 6.15, 1.18,
               "-- 课程排名 + 条件聚合（#2）\n"
               "RANK() OVER (ORDER BY count(DISTINCT m.id) DESC, c.code)\n"
               "count(DISTINCT m.id) FILTER (WHERE m.sender = 'user')", size=9.4)
    code_block(slide, 0.72, 2.92, 6.15, 1.18,
               "-- 补齐没有消息的月份（#3）\n"
               "generate_series(start_month, current_month, '1 month')\n"
               "LEFT JOIN buckets USING (month_start)  -- coalesce 补 0", size=9.4)
    code_block(slide, 0.72, 4.26, 6.15, 1.5,
               "-- 先修闭包：递归 CTE + 最短深度（#21）\n"
               "WITH RECURSIVE walk AS (\n"
               "  SELECT id AS tag, id AS prereq, 0 AS depth, ARRAY[id] AS path\n"
               "  UNION ALL\n"
               "  SELECT w.tag, e.from_tag_id, w.depth + 1, w.path || e.from_tag_id\n"
               "    FROM walk w JOIN app.tag_edges e ON e.to_tag_id = w.prereq\n"
               "   WHERE NOT e.from_tag_id = ANY (w.path))\n"
               "SELECT DISTINCT ON (tag, prereq) ... ORDER BY depth;", size=8.5)
    text(slide, 0.72, 5.93, 6.15, 0.55,
         "右侧输出就是刚才执行的结果；\n"
         "sql/showcase.sql #2 / #3 / #21 可直接复跑。", size=10.5, color=MUTED, spacing=1.18)

    rows = [
        ["课程", "消息", "用户/助手", "排名"],
        ["DB", "84", "42 / 42", "1"],
        ["ML", "84", "42 / 42", "2"],
        ["NET", "84", "42 / 42", "3"],
        ["OS", "84", "42 / 42", "4"],
        ["DS", "42", "21 / 21", "5"],
    ]
    table(slide, 7.18, 1.58, 5.44, 2.08, rows, [1.55, 1.05, 1.55, 1.29],
          font_size=10, header_size=10)
    text(slide, 7.18, 3.7, 5.44, 0.28, "GROUP BY + FILTER + RANK  →  排名 1–5",
         size=10, color=MUTED)

    closure = [
        ["后学", "先修", "深度"],
        ["B+树", "SQL", "2"],
        ["AVL", "二叉树", "2"],
        ["LRU", "虚拟内存", "3"],
        ["锁", "事务", "3"],
    ]
    table(slide, 7.18, 4.02, 3.2, 1.5, closure, [1.15, 1.25, 0.8],
          font_size=10, header_size=10)
    text(slide, 10.5, 4.06, 2.12, 0.9, "10 对间接先修\n只列最短深度的前 4 行",
         size=10, color=MUTED, spacing=1.2)

    rect(slide, 7.18, 5.74, 5.44, 1.1, SOFT, radius=True)
    text(slide, 7.38, 5.84, 5.04, 0.94,
         "为什么讲这三条\n"
         "• 排名：窗口函数不丢明细行\n"
         "• 补零：骨架表 LEFT JOIN，缺月显示 0\n"
         "• 闭包：path 数组防环，DISTINCT ON 取最短", size=10.2, color=GREEN, spacing=1.18)
    notes(slide, "1:15 三条查询各 20 秒：排名/补零/递归闭包；指出结果表就是刚才执行的真实输出，"
                 "不要求观众相信截图。")


def slide_index(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    header(slide, "04 / 索引与执行计划 · 1:00", "420 行的小表：规划器选顺序扫描，索引为大表保留", 5)

    code_block(slide, 0.72, 1.58, 6.2, 1.3,
               "Seq Scan on messages  (actual rows=1)\n"
               "  Filter: content ~~* '%MVCC%'\n"
               "  Rows Removed by Filter: 419\n"
               "  Buffers: shared hit=9\n"
               "  Execution Time: 0.257 ms", size=10.2)
    code_block(slide, 0.72, 3.05, 6.2, 1.4,
               "SET enable_seqscan = off;\n"
               "Bitmap Heap Scan on messages  (actual rows=1)\n"
               "  Recheck Cond: content ~~* '%MVCC%'\n"
               "  -> Bitmap Index Scan on messages_content_trgm\n"
               "  Execution Time: 0.026 ms", size=10.2)
    text(slide, 0.72, 4.65, 6.2, 0.55,
         "同一查询、同一数据：只有强制关闭顺序扫描后 GIN 索引才生效。\n"
         "耗时随缓存变化，两套方案都在亚毫秒级。", size=10.5, color=MUTED)
    code_block(slide, 0.72, 5.38, 6.2, 1.28,
               "-- 支撑上述查询的三个索引（节选）\n"
               "CREATE INDEX messages_content_trgm ON app.messages\n"
               "    USING gin (content gin_trgm_ops);\n"
               "CREATE INDEX messages_content_md5_idx ON app.messages ((md5(content)));\n"
               "CREATE INDEX review_items_queue_idx ON app.review_items\n"
               "    (user_id, status, due_date) WHERE status <> 'mastered';", size=8.6)

    text(slide, 7.2, 1.58, 5.42, 0.32, "索引设计", size=12.5, bold=True, color=GREEN)
    bullets(slide, 7.2, 1.98, 5.42, 4.5, [
        "messages(content) GIN + pg_trgm：中文子串 ILIKE",
        "md5(content) 表达式索引：跨会话重复检测",
        "review_items 部分索引：WHERE status <> 'mastered'",
        "唯一/部分唯一索引承担业务约束：外部 ID、每课一个 owner",
        "会话标题 trigram 索引：全局搜索",
        "可选 HNSW 向量索引：没有模型时完全不用",
    ], size=11.5, gap=7)
    rect(slide, 7.2, 5.55, 5.42, 1.1, RGBColor(0xF2, 0xE4, 0xD8), radius=True)
    text(slide, 7.4, 5.66, 5.02, 0.88,
         "结论：不宣称“所有查询都走索引”。索引按访问模式设计，\n"
         "代价模型在小表上选择顺序扫描是正常行为。", size=11, bold=True, color=RUST)
    notes(slide, "1:00 讲执行计划对比：估代价 vs 实测；说明为什么不对 420 行表强行走索引，"
                 "以及五个索引各自服务的查询。")


def slide_permissions(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    header(slide, "05 / 访问控制 · 1:10", "同一视图、不同 user_id：行级权限在数据库内完成", 6)

    rows = [
        ["操作", "owner", "editor", "viewer", "admin"],
        ["查看课程 / 会话", "✓", "✓", "✓", "仅聚合"],
        ["编辑课程 / 共享", "✓", "—", "—", "—"],
        ["导入 / 归类", "✓", "✓", "—", "—"],
        ["批注 / 复习", "✓", "✓", "—", "—"],
        ["删除课程", "✓", "—", "—", "—"],
        ["管理用户", "—", "—", "—", "✓"],
    ]
    table(slide, 0.72, 1.6, 6.35, 2.8, rows, [1.9, 1.0, 1.0, 1.0, 1.45], font_size=10.5, header_size=10.5)

    layers = [
        ("应用层", "角色判断，越权直接 403"),
        ("SQL 作用域", "owner_id = uid OR EXISTS course_members"),
        ("数据库", "security_barrier 视图 + t2s_reader 只读角色"),
    ]
    for index, (name, body) in enumerate(layers):
        y = 1.6 + index * 0.92
        rect(slide, 7.38, y, 5.24, 0.8, WHITE, radius=True, line=LINE, width=0.7)
        text(slide, 7.56, y + 0.1, 1.75, 0.3, name, size=12.5, bold=True, color=GREEN)
        text(slide, 9.3, y + 0.1, 3.15, 0.62, body, size=10.2, color=MUTED, spacing=1.1)

    text(slide, 0.72, 4.58, 6.35, 0.62,
         "证据：同一个 t2s.v_my_course_stats，alice 看到 5 门课，bob 只看到 2 门\n"
         "（DB viewer、OS editor）；视图内按 app.user_id 过滤，不依赖前端拼条件。",
         size=10.8, color=GREEN, spacing=1.18)
    picture_fit(slide, "15-permission-denied.png", 0.72, 5.32, 3.55, 1.5,
                crop=(0.0, 0.0, 0.585, 0.47))
    text(slide, 4.5, 5.4, 2.6, 1.3,
         "bob 是数据库课程 viewer，\n直接访问编辑页被 403。\n"
         "RLS 对比实验用同一规则\n验证数据库层过滤。", size=10, color=MUTED, spacing=1.18)
    notes(slide, "1:10 先讲矩阵，再讲三层防护：应用层 403、SQL 作用域、视图与只读角色；"
                 "现场可展示 bob 被拒页面。")


def slide_tx(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    header(slide, "06 / 事务与并发 · 1:00", "一次导入只写一次：确认阶段全有或全无", 7)

    steps = [
        ("1", "上传", "JSON / MD\n≤ 5 MB"),
        ("2", "解析校验", "字段、角色\n时间、上限"),
        ("3", "预览", "JSONB + token\n30 分钟"),
        ("4", "勾选归类", "课程 / 标签\n查重状态"),
        ("5", "单事务入库", "FOR UPDATE\nadvisory lock"),
    ]
    for index, (number, name, body) in enumerate(steps):
        x = 0.72 + index * 2.47
        rect(slide, x, 1.58, 2.15, 1.4, WHITE, radius=True, line=LINE, width=0.7)
        text(slide, x + 0.16, 1.68, 0.4, 0.34, number, size=17, bold=True, color=RUST)
        text(slide, x + 0.58, 1.7, 1.45, 0.3, name, size=12, bold=True, color=INK)
        text(slide, x + 0.16, 2.13, 1.85, 0.7, body, size=10, color=MUTED, spacing=1.15)
        if index < len(steps) - 1:
            text(slide, x + 2.12, 2.0, 0.36, 0.4, "→", size=14, bold=True, color=GREEN)

    code_block(slide, 0.72, 3.16, 6.55, 1.7,
               "BEGIN;\n"
               "SELECT status FROM app.import_batches\n"
               " WHERE id = %(batch)s FOR UPDATE;      -- 防重复确认\n"
               "SELECT pg_advisory_xact_lock(%(source_id)s);\n"
               "INSERT INTO app.messages ...\n"
               "  ON CONFLICT (conversation_id, seq_no) DO NOTHING;\n"
               "COMMIT;                                -- 任一步失败整体回滚", size=9.6)

    bullets(slide, 0.72, 5.1, 6.55, 1.35, [
        "会话按 (source_id, external_id) 更新标题；消息按序号跳过",
        "实测：INSERT 0 1（更新 1 行）、INSERT 0 0（重复消息）",
        "5 个批次共 30 会话 / 420 消息，重复确认后计数不变",
    ], size=10.5, gap=3)

    text(slide, 7.5, 3.12, 5.1, 0.3, "复习打卡：先锁行，再写回新间隔", size=11.5, bold=True, color=GREEN)
    code_block(slide, 7.5, 3.52, 5.1, 1.85,
               "SELECT ... FROM app.review_items\n"
               " WHERE id = %(id)s FOR UPDATE;\n"
               "-- 应用层用 FSRS 计算 good 评分\n"
               "UPDATE app.review_items\n"
               "   SET interval_days = 16,\n"
               "       due_date = current_date + 16,\n"
               "       fsrs_stability = 16.0835\n"
               " WHERE id = %(id)s AND user_id = %(uid)s;", size=9.2)
    text(slide, 7.5, 5.55, 5.1, 0.95,
         "示例：逾期 8 天后复习，间隔 1 天 → 16 天；\n"
         "数据库只保存调度结果，避免并发打卡互相覆盖。", size=10.2, color=MUTED, spacing=1.15)
    notes(slide, "1:00 讲预览不落业务表、确认单事务；两个锁的作用；ON CONFLICT 的幂等结果；"
                 "最后 15 秒讲复习打卡的 FOR UPDATE。")


def slide_triggers(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    header(slide, "07 / 触发器、审计与观测 · 0:50", "约束不只写在应用里：数据库自己拒绝坏数据", 8)

    card(slide, 0.72, 1.58, 5.8, 1.42, "先修边防环",
         "写入前从后继出发递归展开，能回到先修就 RAISE。\n32 条边构成 DAG，视图实时算闭包，不落冗余表。",
         number="01")
    card(slide, 6.82, 1.58, 5.8, 1.42, "每门课恰好一个 owner",
         "部分唯一索引阻止“多于 1”；延迟约束触发器在事务结束时\n检查“至少 1”，破坏时返回 SQLSTATE 23514。",
         number="02", accent=RUST)
    card(slide, 0.72, 3.18, 5.8, 1.42, "审计只记元数据",
         "3 张结构表的 AFTER 触发器：表名、动作、主键、变更列名；\n不保存列值，password_hash 显式排除。",
         number="03", accent=GREEN_DARK)
    card(slide, 6.82, 3.18, 5.8, 1.42, "只读观测",
         "管理员只读 pg_stat_* 与白名单 EXPLAIN(JSON)；\n应用角色对 audit_log 可读不可写（can_insert = f）。",
         number="04", accent=RUST)

    code_block(slide, 0.72, 4.86, 11.9, 1.42,
               "-- 变更列 = 新旧行 JSONB 的键差异，只存列名\n"
               "changed := (SELECT array_agg(key)\n"
               "              FROM jsonb_each(to_jsonb(NEW)) n\n"
               "             WHERE n.value IS DISTINCT FROM (to_jsonb(OLD) -> n.key));\n"
               "INSERT INTO app.audit_log (table_name, action, row_pk, changed_columns) ...",
               size=9.6)
    notes(slide, "0:50 四个约束各一句；强调触发器只做结构校验与元数据审计，不复制业务数据，"
                 "因此管理员可观测但读不到正文。")


def slide_end(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    rect(slide, 0, 0, W, H, GREEN_DARK)
    text(slide, 0.85, 0.78, 7.6, 0.32, "08 / 结论", size=12, bold=True, color=CREAM)
    text(slide, 0.85, 1.35, 8.6, 0.9, "一个可执行、可复现的\n数据库课程项目", size=33, bold=True,
         color=WHITE, spacing=1.18)

    stats = [("17", "张表 / 15 视图"), ("10", "个触发器"), ("54", "个索引"), ("23", "条 showcase SQL")]
    for index, (value, label) in enumerate(stats):
        x = 0.85 + index * 1.78
        rect(slide, x, 2.95, 1.62, 1.15, RGBColor(0x2B, 0x50, 0x44), radius=True)
        text(slide, x + 0.12, 3.05, 1.4, 0.5, value, size=24, bold=True, color=WHITE)
        text(slide, x + 0.12, 3.62, 1.4, 0.32, label, size=8.5, color=CREAM)

    bullets(slide, 0.85, 4.45, 6.6, 2.2, [
        "关系模型、约束与范式取舍都能在 DDL 中找到依据",
        "关键查询覆盖窗口、CTE、LATERAL、DISTINCT ON、递归",
        "每条结果来自 sql/showcase.sql，42 项验收检查可复跑",
    ], size=13, color=PALE, gap=8, bullet_color=CREAM)

    rect(slide, 7.85, 1.4, 4.6, 4.35, RGBColor(0x2B, 0x50, 0x44), radius=True)
    text(slide, 8.08, 1.62, 4.15, 0.32, "已知限制", size=12.5, bold=True, color=CREAM)
    bullets(slide, 8.08, 2.12, 4.1, 3.2, [
        "单机课程项目，未做分布式与高并发",
        "420 行数据下多数查询走顺序扫描",
        "模拟数据，不含真实个人信息",
        "AI 能力可选，未配置时走规则模式",
    ], size=10.8, color=RGBColor(0xC9, 0xD3, 0xCB), gap=9,
        bullet_color=RGBColor(0xB4, 0x69, 0x3F))

    text(slide, 0.85, 6.4, 6.0, 0.5, "谢谢，欢迎提问", size=20, bold=True, color=WHITE)
    text(slide, 0.85, 6.95, 9.0, 0.3,
         "sql/showcase.sql · docs/sql-showcase.md · docs/design.md · docs/report.md",
         size=9.5, color=RGBColor(0x9F, 0xB3, 0xA6))
    notes(slide, "0:25 一句结论 + 主动说明限制：不把 AI 与在线演示作为正确性证明，"
                 "所有数据库结论都可本地复跑。")


def build() -> Path:
    prs = Presentation()
    prs.slide_width = Inches(W)
    prs.slide_height = Inches(H)
    prs.core_properties.title = "个人学习空间：数据库设计阶段性汇报"
    prs.core_properties.author = "《数据库系统概论》大作业"
    prs.core_properties.subject = "选题 2 · 数据库应用系统"
    prs.core_properties.keywords = "PostgreSQL, E-R, SQL, 索引, 事务, 触发器, 权限"

    title_slide(prs)
    slide_model(prs)
    slide_schema(prs)
    slide_queries(prs)
    slide_index(prs)
    slide_permissions(prs)
    slide_tx(prs)
    slide_triggers(prs)
    slide_end(prs)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    prs.save(OUT)
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"已生成 {path.relative_to(ROOT)}（9 页，8 分钟版）")
