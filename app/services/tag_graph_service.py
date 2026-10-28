from __future__ import annotations

from collections import defaultdict

from psycopg import errors

from ..errors import ConflictError, NotFoundError, ValidationError

NODE_WIDTH = 148
NODE_HEIGHT = 42
X_GAP = 200
Y_GAP = 76
MARGIN = 28
MIN_WIDTH = 360
MIN_HEIGHT = 180


def _owned_tag(conn, user_id: int, tag_id: int) -> dict:
    row = conn.execute(
        "SELECT id, name, kind, color FROM app.tags WHERE id = %s AND owner_id = %s",
        (tag_id, user_id),
    ).fetchone()
    if row is None:
        raise NotFoundError("标签不存在")
    return row


def _tag_rows(conn, user_id: int) -> list[dict]:
    return conn.execute(
        """
        WITH RECURSIVE roots AS (
            SELECT t.id
              FROM app.tags t
             WHERE t.owner_id = %(uid)s
               AND NOT EXISTS (
                   SELECT 1 FROM app.tag_edges e WHERE e.to_tag_id = t.id
               )
        ),
        walk AS (
            SELECT id, 0 AS depth FROM roots
            UNION ALL
            SELECT e.to_tag_id, w.depth + 1
              FROM walk w
              JOIN app.tag_edges e ON e.from_tag_id = w.id
        )
        SELECT t.id, t.name, t.kind, t.color,
               coalesce(max(w.depth), 0)::int AS level,
               (SELECT count(DISTINCT ct.conversation_id)
                  FROM app.conversation_tags ct
                 WHERE ct.tag_id = t.id) AS usage_count,
               (SELECT mode() WITHIN GROUP (ORDER BY c.name)
                  FROM app.conversation_tags ct
                  JOIN app.conversations v ON v.id = ct.conversation_id
                  JOIN app.courses c ON c.id = v.course_id
                 WHERE ct.tag_id = t.id) AS course_name,
               EXISTS (
                   SELECT 1 FROM app.tag_edges e
                    WHERE e.from_tag_id = t.id OR e.to_tag_id = t.id
               ) AS connected
          FROM app.tags t
          LEFT JOIN walk w ON w.id = t.id
         WHERE t.owner_id = %(uid)s
         GROUP BY t.id, t.name, t.kind, t.color
         ORDER BY t.name
        """,
        {"uid": user_id},
    ).fetchall()


def _edge_rows(conn, user_id: int) -> list[dict]:
    return conn.execute(
        """
        SELECT e.id, e.from_tag_id, e.to_tag_id,
               tf.name AS from_name, tt.name AS to_name
          FROM app.tag_edges e
          JOIN app.tags tf ON tf.id = e.from_tag_id
          JOIN app.tags tt ON tt.id = e.to_tag_id
         WHERE tf.owner_id = %(uid)s AND tt.owner_id = %(uid)s
         ORDER BY e.id
        """,
        {"uid": user_id},
    ).fetchall()


def _components(tag_ids: list[int], edges: list[dict]) -> list[list[int]]:
    parent = {tag_id: tag_id for tag_id in tag_ids}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for edge in edges:
        left, right = find(edge["from_tag_id"]), find(edge["to_tag_id"])
        if left != right:
            parent[right] = left

    groups: dict[int, list[int]] = defaultdict(list)
    for tag_id in tag_ids:
        groups[find(tag_id)].append(tag_id)
    return list(groups.values())


def _layout(nodes: list[dict], edges: list[dict], selected_id: int | None) -> dict:
    by_level: dict[int, list[dict]] = defaultdict(list)
    for node in nodes:
        by_level[node["level"]].append(node)

    min_level = min(by_level)
    positions: dict[int, tuple[int, int]] = {}
    max_rows = 1
    for level in sorted(by_level):
        column = sorted(by_level[level], key=lambda row: row["name"])
        max_rows = max(max_rows, len(column))
        for index, node in enumerate(column):
            positions[node["id"]] = (
                MARGIN + (level - min_level) * X_GAP,
                MARGIN + index * Y_GAP,
            )

    width = max(
        MIN_WIDTH,
        MARGIN * 2 + (max(by_level) - min_level + 1) * X_GAP - (X_GAP - NODE_WIDTH),
    )
    height = max(MIN_HEIGHT, MARGIN * 2 + max_rows * Y_GAP - (Y_GAP - NODE_HEIGHT))

    laid_nodes = []
    for node in nodes:
        x, y = positions[node["id"]]
        laid_nodes.append(
            {
                **node,
                "x": x,
                "y": y,
                "width": NODE_WIDTH,
                "height": NODE_HEIGHT,
                "selected": node["id"] == selected_id,
            }
        )

    paths = []
    for edge in edges:
        start = positions[edge["from_tag_id"]]
        end = positions[edge["to_tag_id"]]
        x1, y1 = start[0] + NODE_WIDTH, start[1] + NODE_HEIGHT // 2
        x2, y2 = end[0], end[1] + NODE_HEIGHT // 2
        control = max(36, (x2 - x1) // 2)
        paths.append(
            {
                **edge,
                "path": f"M {x1},{y1} C {x1 + control},{y1} {x2 - control},{y2} {x2},{y2}",
            }
        )

    return {
        "width": width,
        "height": height,
        "nodes": laid_nodes,
        "paths": paths,
    }


def graph(conn, user_id: int, selected_id: int | None = None) -> dict:
    tags = _tag_rows(conn, user_id)
    edges = _edge_rows(conn, user_id)
    tag_by_id = {tag["id"]: tag for tag in tags}
    connected_ids = [tag["id"] for tag in tags if tag["connected"]]

    by_label: dict[str, dict] = {}
    for tag_ids in _components(connected_ids, edges):
        member_ids = set(tag_ids)
        member_edges = [
            edge
            for edge in edges
            if edge["from_tag_id"] in member_ids and edge["to_tag_id"] in member_ids
        ]
        names = [
            tag_by_id[tag_id]["course_name"]
            for tag_id in tag_ids
            if tag_by_id[tag_id]["course_name"]
        ]
        label = max(set(names), key=names.count) if names else "跨课程"
        group = by_label.setdefault(label, {"ids": set(), "edges": []})
        group["ids"].update(tag_ids)
        group["edges"].extend(member_edges)

    components = []
    for label, group in by_label.items():
        layout = _layout(
            [tag_by_id[tag_id] for tag_id in group["ids"]], group["edges"], selected_id
        )
        components.append(
            {
                "label": label,
                "tag_count": len(group["ids"]),
                "edge_count": len(group["edges"]),
                **layout,
            }
        )
    components.sort(key=lambda item: (-item["tag_count"], item["label"]))

    return {
        "tags": tags,
        "edges": edges,
        "components": components,
        "isolated": [tag for tag in tags if not tag["connected"]],
        "tag_count": len(tags),
        "edge_count": len(edges),
        "selected": tag_detail(conn, user_id, selected_id) if selected_id else None,
    }


def tag_detail(conn, user_id: int, tag_id: int) -> dict:
    tag = _owned_tag(conn, user_id, tag_id)
    prerequisites = conn.execute(
        """
        WITH RECURSIVE walk(tag_id, depth, path) AS (
            SELECT e.from_tag_id, 1, ARRAY[e.to_tag_id, e.from_tag_id]
              FROM app.tag_edges e
             WHERE e.to_tag_id = %(tag_id)s
            UNION ALL
            SELECT e.from_tag_id, w.depth + 1, w.path || e.from_tag_id
              FROM walk w
              JOIN app.tag_edges e ON e.to_tag_id = w.tag_id
             WHERE NOT e.from_tag_id = ANY(w.path)
        )
        SELECT t.id, t.name, t.kind,
               min(w.depth)::int AS depth
          FROM walk w
          JOIN app.tags t ON t.id = w.tag_id
         WHERE t.owner_id = %(uid)s
         GROUP BY t.id, t.name, t.kind
         ORDER BY depth, t.name
        """,
        {"tag_id": tag_id, "uid": user_id},
    ).fetchall()

    unlocks = conn.execute(
        """
        SELECT t.id, t.name, t.kind
          FROM app.tag_edges e
          JOIN app.tags t ON t.id = e.to_tag_id
         WHERE e.from_tag_id = %s AND t.owner_id = %s
         ORDER BY t.name
        """,
        (tag_id, user_id),
    ).fetchall()

    conversations = conn.execute(
        """
        SELECT DISTINCT v.id, v.title, c.name AS course_name
          FROM app.conversation_tags ct
          JOIN app.conversations v ON v.id = ct.conversation_id
          LEFT JOIN app.courses c ON c.id = v.course_id
         WHERE ct.tag_id = %s
         ORDER BY v.id
         LIMIT 6
        """,
        (tag_id,),
    ).fetchall()

    return {
        "tag": tag,
        "prerequisites": prerequisites,
        "unlocks": unlocks,
        "conversations": conversations,
    }


def add_edge(conn, user_id: int, from_tag_id: int, to_tag_id: int) -> int:
    _owned_tag(conn, user_id, from_tag_id)
    _owned_tag(conn, user_id, to_tag_id)
    if from_tag_id == to_tag_id:
        raise ValidationError("先修标签与后继标签不能相同")
    try:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO app.tag_edges (from_tag_id, to_tag_id) VALUES (%s, %s) RETURNING id",
                (from_tag_id, to_tag_id),
            ).fetchone()
    except errors.UniqueViolation as exc:
        raise ConflictError("这条先修关系已经存在") from exc
    except errors.CheckViolation as exc:
        raise ValidationError("这条先修关系会形成环，先修图必须是有向无环图") from exc
    return row["id"]


def delete_edge(conn, user_id: int, edge_id: int) -> None:
    with conn.transaction():
        row = conn.execute(
            """
            DELETE FROM app.tag_edges e
             USING app.tags tf
             WHERE e.id = %s
               AND tf.id = e.from_tag_id
               AND tf.owner_id = %s
            RETURNING e.id
            """,
            (edge_id, user_id),
        ).fetchone()
    if row is None:
        raise NotFoundError("先修关系不存在")
