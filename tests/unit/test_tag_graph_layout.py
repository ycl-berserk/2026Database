from __future__ import annotations

from app.services.tag_graph_service import NODE_HEIGHT, NODE_WIDTH, _components, _layout


def test_components_follow_edges():
    components = _components(
        [1, 2, 3, 4],
        [
            {"from_tag_id": 1, "to_tag_id": 2},
            {"from_tag_id": 2, "to_tag_id": 3},
        ],
    )
    sizes = sorted(len(group) for group in components)
    assert sizes == [1, 3]
    assert {1, 2, 3} in [set(group) for group in components]


def test_layout_places_levels_and_paths():
    nodes = [
        {"id": 1, "name": "索引", "level": 0, "color": "#2f5d4e"},
        {"id": 2, "name": "B+树", "level": 1, "color": "#a8432c"},
    ]
    edges = [{"id": 7, "from_tag_id": 1, "to_tag_id": 2, "from_name": "索引", "to_name": "B+树"}]
    layout = _layout(nodes, edges, selected_id=2)

    by_id = {node["id"]: node for node in layout["nodes"]}
    assert by_id[1]["x"] < by_id[2]["x"]
    assert by_id[2]["selected"] is True
    assert by_id[1]["width"] == NODE_WIDTH and by_id[1]["height"] == NODE_HEIGHT
    assert layout["width"] >= by_id[2]["x"] + NODE_WIDTH
    assert layout["height"] >= by_id[1]["y"] + NODE_HEIGHT

    path = layout["paths"][0]["path"]
    assert path.startswith(f"M {by_id[1]['x'] + NODE_WIDTH},")
    assert path.endswith(f"{by_id[2]['x']},{by_id[2]['y'] + NODE_HEIGHT // 2}")
