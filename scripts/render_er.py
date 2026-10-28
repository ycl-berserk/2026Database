"""生成教材风格（Chen 记法）的 E-R 图 SVG，并输出 Mermaid ER 伴随文件。"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
WIDTH, HEIGHT = 1480, 1250

COLORS = {
    "bg": "#fbfaf6",
    "ink": "#26322c",
    "entity_fill": "#edf4ee",
    "entity_stroke": "#2f5d4e",
    "rel_fill": "#f6ead7",
    "rel_stroke": "#a6771f",
    "attr_fill": "#fffdf8",
    "attr_stroke": "#8a8f89",
    "line": "#6f7b72",
    "card": "#a8432c",
    "muted": "#6b746f",
}

ENTITIES = {
    "user": (760, 90, 170, 76, "用户"),
    "course": (190, 280, 170, 76, "课程"),
    "source": (760, 280, 170, 76, "来源"),
    "tag": (1330, 280, 170, 76, "标签"),
    "conv": (760, 480, 180, 76, "会话"),
    "msg": (760, 680, 180, 76, "消息"),
    "anno": (330, 940, 180, 76, "批注"),
    "review": (1160, 940, 190, 76, "复习项"),
}

RELATIONSHIPS = {
    "create": (470, 170, "创建"),
    "member": (330, 410, "加入"),
    "own": (760, 185, "拥有"),
    "define": (1050, 170, "定义"),
    "export": (760, 380, "导出"),
    "assign": (470, 380, "归属"),
    "contain": (760, 580, "包含"),
    "tagging": (1060, 380, "打标签"),
    "annotate": (545, 810, "批注于"),
    "write": (500, 550, "撰写"),
    "reviewing": (745, 940, "复习"),
    "queue": (980, 660, "加入复习"),
}

ATTRIBUTES = [
    (500, 50, "用户ID", True),
    (1020, 50, "用户名", True),
    (100, 175, "课程ID", True),
    (90, 395, "课程编号", True),
    (550, 235, "来源ID", True),
    (970, 235, "平台账号", False),
    (1330, 145, "标签ID", True),
    (1330, 420, "名称", False),
    (560, 470, "会话ID", True),
    (960, 470, "外部ID", True),
    (560, 670, "消息ID", True),
    (960, 670, "序号", False),
    (120, 920, "批注ID", True),
    (330, 1045, "类型", False),
    (1370, 920, "复习项ID", True),
    (1160, 1045, "到期日", False),
    (190, 505, "角色", True),
    (1060, 495, "标注人", False),
]

ATTRIBUTE_LINKS = [
    ((500, 50), (690, 70)),
    ((1020, 50), (835, 75)),
    ((100, 175), (140, 242)),
    ((90, 395), (160, 318)),
    ((550, 235), (675, 265)),
    ((970, 235), (845, 265)),
    ((1330, 145), (1330, 242)),
    ((1330, 420), (1330, 318)),
    ((560, 470), (670, 480)),
    ((960, 470), (850, 480)),
    ((560, 670), (670, 680)),
    ((960, 670), (850, 680)),
    ((120, 920), (240, 935)),
    ((330, 1045), (330, 978)),
    ((1370, 920), (1255, 935)),
    ((1160, 1045), (1160, 978)),
    ((190, 505), (300, 430)),
    ((1060, 495), (1060, 413)),
]

RELATION_LINKS = [
    ((690, 105), (470, 170)),
    ((470, 170), (275, 265)),
    ((700, 128), (330, 410)),
    ((330, 410), (200, 318)),
    ((760, 128), (760, 185)),
    ((760, 185), (760, 242)),
    ((830, 105), (1050, 170)),
    ((1050, 170), (1245, 265)),
    ((760, 318), (760, 380)),
    ((760, 380), (760, 442)),
    ((250, 318), (470, 380)),
    ((470, 380), (680, 470)),
    ((760, 518), (760, 580)),
    ((760, 580), (760, 642)),
    ((840, 455), (1060, 380)),
    ((1060, 380), (1280, 318)),
    ((710, 715), (545, 810)),
    ((545, 810), (405, 915)),
    ((700, 120), (500, 550)),
    ((500, 550), (290, 905)),
    ((420, 940), (745, 940)),
    ((745, 940), (1065, 940)),
    ((835, 115), (980, 660)),
    ((980, 660), (1160, 902)),
]

CARDINALITIES = [
    (610, 138, "1"), (360, 212, "N"),
    (525, 320, "M"), (270, 378, "N"),
    (782, 160, "1"), (782, 220, "N"),
    (928, 138, "1"), (1170, 218, "N"),
    (782, 340, "1"), (782, 408, "N"),
    (378, 340, "1"), (592, 422, "N"),
    (782, 540, "1"), (782, 610, "N"),
    (930, 430, "M"), (1192, 338, "N"),
    (642, 748, "1"), (472, 852, "N"),
    (634, 285, "1"), (424, 742, "N"),
    (638, 916, "1"), (902, 916, "N"),
    (862, 205, "1"), (1040, 782, "N"),
]


def entity(cx: int, cy: int, w: int, h: int, label: str) -> str:
    x, y = cx - w / 2, cy - h / 2
    return (
        f'<rect x="{x:.0f}" y="{y:.0f}" width="{w}" height="{h}" rx="10" '
        f'fill="{COLORS["entity_fill"]}" stroke="{COLORS["entity_stroke"]}" stroke-width="2.4"/>'
        f'<text x="{cx}" y="{cy + 1}" text-anchor="middle" dominant-baseline="middle" '
        f'font-size="24" font-weight="700" fill="{COLORS["ink"]}">{escape(label)}</text>'
    )


def relationship(cx: int, cy: int, label: str) -> str:
    w, h = 150, 70
    points = f"{cx},{cy - h / 2} {cx + w / 2},{cy} {cx},{cy + h / 2} {cx - w / 2},{cy}"
    return (
        f'<polygon points="{points}" fill="{COLORS["rel_fill"]}" '
        f'stroke="{COLORS["rel_stroke"]}" stroke-width="2"/>'
        f'<text x="{cx}" y="{cy + 1}" text-anchor="middle" dominant-baseline="middle" '
        f'font-size="19" font-weight="600" fill="{COLORS["ink"]}">{escape(label)}</text>'
    )


def attribute(cx: int, cy: int, label: str, key: bool) -> str:
    decoration = ' text-decoration="underline"' if key else ""
    return (
        f'<ellipse cx="{cx}" cy="{cy}" rx="82" ry="31" fill="{COLORS["attr_fill"]}" '
        f'stroke="{COLORS["attr_stroke"]}" stroke-width="1.8"/>'
        f'<text x="{cx}" y="{cy + 1}" text-anchor="middle" dominant-baseline="middle" '
        f'font-size="18" fill="{COLORS["ink"]}"{decoration}>{escape(label)}</text>'
    )


def line(x1: int, y1: int, x2: int, y2: int, dashed: bool = False) -> str:
    dash = ' stroke-dasharray="7 6"' if dashed else ""
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{COLORS["line"]}" '
        f'stroke-width="1.7"{dash}/>'
    )


def build_svg() -> str:
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" font-family="Microsoft YaHei, Noto Sans CJK SC, sans-serif">',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{COLORS["bg"]}"/>',
        f'<text x="{WIDTH / 2}" y="46" text-anchor="middle" font-size="30" font-weight="700" '
        f'fill="{COLORS["ink"]}">个人学习空间 · 核心 E-R 图（Chen 记法）</text>',
        f'<text x="{WIDTH / 2}" y="76" text-anchor="middle" font-size="16" '
        f'fill="{COLORS["muted"]}">8 个核心实体 · 12 组联系 · 批注与复习项为弱实体</text>',
    ]

    parts.append('<g transform="translate(0,70)">')
    for start, end in ATTRIBUTE_LINKS:
        parts.append(line(*start, *end))
    for start, end in RELATION_LINKS:
        parts.append(line(*start, *end))

    for x, y, label, key in ATTRIBUTES:
        parts.append(attribute(x, y, label, key))

    for cx, cy, w, h, label in ENTITIES.values():
        parts.append(entity(cx, cy, w, h, label))

    for cx, cy, label in RELATIONSHIPS.values():
        parts.append(relationship(cx, cy, label))

    for x, y, label in CARDINALITIES:
        parts.append(
            f'<text x="{x}" y="{y}" text-anchor="middle" font-size="19" font-weight="700" '
            f'fill="{COLORS["card"]}">{label}</text>'
        )

    legend = [
        "图例：矩形 = 实体；菱形 = 联系；椭圆 = 属性；下划线 = 主键 / 唯一键",
        "基数：1 : N 一对多；M : N 多对多；会话对课程为可选归属（0..1），删课程置空",
        "说明：导入批次与 Text2SQL 日志为操作型实体，未在本图展开；完整属性见 docs/design.md",
    ]
    for index, text in enumerate(legend):
        parts.append(
            f'<text x="80" y="{1100 + index * 27}" font-size="16" '
            f'fill="{COLORS["muted"]}">{escape(text)}</text>'
        )

    parts.append("</g>")
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


MERMAID = """erDiagram
    users ||--o{ courses : "创建"
    users ||--o{ course_members : "加入"
    courses ||--o{ course_members : "包含"
    users ||--o{ sources : "拥有"
    sources ||--o{ conversations : "导出"
    courses |o--o{ conversations : "归属"
    conversations ||--o{ messages : "包含"
    users ||--o{ tags : "定义"
    conversations ||--o{ conversation_tags : "打标签"
    tags ||--o{ conversation_tags : "被使用"
    messages ||--o{ annotations : "被批注"
    users ||--o{ annotations : "撰写"
    annotations ||--o{ review_items : "复习"
    users ||--o{ review_items : "加入复习"
    sources ||--o{ import_batches : "导入"
    users ||--o{ import_batches : "发起"
    users ||--o{ text2sql_log : "提问"

    users {
        bigint id PK
        varchar username UK
        varchar display_name
        varchar system_role
        boolean is_active
    }
    courses {
        bigint id PK
        varchar code
        varchar name
        varchar semester
        bigint created_by FK
    }
    course_members {
        bigint course_id PK, FK
        bigint user_id PK, FK
        varchar role
        timestamptz joined_at
    }
    sources {
        bigint id PK
        bigint owner_id FK
        varchar platform
        varchar account_label
    }
    import_batches {
        bigint id PK
        bigint user_id FK
        bigint source_id FK
        varchar status
        jsonb payload
    }
    conversations {
        bigint id PK
        bigint source_id FK
        bigint course_id FK
        varchar external_id
        varchar title
    }
    messages {
        bigint id PK
        bigint conversation_id FK
        int seq_no
        varchar sender
        text content
    }
    tags {
        bigint id PK
        bigint owner_id FK
        varchar name
        varchar kind
    }
    conversation_tags {
        bigint conversation_id PK, FK
        bigint tag_id PK, FK
        bigint created_by FK
    }
    annotations {
        bigint id PK
        bigint message_id FK
        bigint user_id FK
        varchar kind
        varchar status
    }
    review_items {
        bigint id PK
        bigint user_id FK
        bigint annotation_id FK
        varchar status
        date due_date
        int interval_days
    }
    text2sql_log {
        bigint id PK
        bigint user_id FK
        text question
        varchar status
    }
"""


def main() -> int:
    DOCS.mkdir(parents=True, exist_ok=True)
    svg_path = DOCS / "er-chen.svg"
    mmd_path = DOCS / "er.mmd"
    svg_path.write_text(build_svg(), encoding="utf-8")
    mmd_path.write_text(MERMAID, encoding="utf-8")
    print(f"[OK] wrote {svg_path}")
    print(f"[OK] wrote {mmd_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
