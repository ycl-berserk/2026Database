from __future__ import annotations

import psycopg
import pytest

from app.errors import ConflictError, NotFoundError, ValidationError
from app.services import tag_graph_service


def _tag(conn, owner_id: int, name: str) -> int:
    return conn.execute(
        "INSERT INTO app.tags (owner_id, name) VALUES (%s, %s) RETURNING id",
        (owner_id, name),
    ).fetchone()["id"]


def _edge(conn, left: int, right: int) -> int:
    return conn.execute(
        "INSERT INTO app.tag_edges (from_tag_id, to_tag_id) VALUES (%s, %s) RETURNING id",
        (left, right),
    ).fetchone()["id"]


def test_add_edge_and_recursive_prerequisites(conn, base):
    alice = base["users"]["alice"]
    index_tag = _tag(conn, alice, "索引")
    btree = _tag(conn, alice, "B+树")
    page = _tag(conn, alice, "页结构")

    tag_graph_service.add_edge(conn, alice, index_tag, btree)
    tag_graph_service.add_edge(conn, alice, btree, page)

    data = tag_graph_service.graph(conn, alice)
    assert data["edge_count"] == 2
    assert len(data["components"]) == 1
    component = data["components"][0]
    assert component["tag_count"] == 3
    levels = {node["name"]: node["level"] for node in component["nodes"]}
    assert levels == {"索引": 0, "B+树": 1, "页结构": 2}
    assert all(node["x"] < 1000 for node in component["nodes"])

    detail = tag_graph_service.tag_detail(conn, alice, page)
    assert [(item["name"], item["depth"]) for item in detail["prerequisites"]] == [
        ("B+树", 1),
        ("索引", 2),
    ]
    assert [item["name"] for item in tag_graph_service.tag_detail(conn, alice, index_tag)["unlocks"]] == [
        "B+树"
    ]

    with pytest.raises(ConflictError):
        tag_graph_service.add_edge(conn, alice, index_tag, btree)


def test_cycles_and_self_edges_are_rejected(conn, base):
    alice = base["users"]["alice"]
    first = _tag(conn, alice, "A")
    second = _tag(conn, alice, "B")
    third = _tag(conn, alice, "C")
    tag_graph_service.add_edge(conn, alice, first, second)
    tag_graph_service.add_edge(conn, alice, second, third)

    with pytest.raises(ValidationError):
        tag_graph_service.add_edge(conn, alice, third, first)
    with pytest.raises(ValidationError):
        tag_graph_service.add_edge(conn, alice, first, first)

    with pytest.raises(psycopg.errors.CheckViolation):
        _edge(conn, third, first)


def test_edge_requires_same_owner(conn, base):
    alice = base["users"]["alice"]
    bob = base["users"]["bob"]
    alice_tag = _tag(conn, alice, "alice-only")
    bob_tag = _tag(conn, bob, "bob-only")

    with pytest.raises(psycopg.errors.CheckViolation):
        _edge(conn, alice_tag, bob_tag)

    with pytest.raises(NotFoundError):
        tag_graph_service.add_edge(conn, alice, alice_tag, bob_tag)

    assert tag_graph_service.graph(conn, bob)["edge_count"] == 0


def test_delete_tag_cascades_edges(conn, base):
    alice = base["users"]["alice"]
    left = _tag(conn, alice, "并发")
    right = _tag(conn, alice, "锁")
    _edge(conn, left, right)

    conn.execute("DELETE FROM app.tags WHERE id = %s", (left,))
    remaining = conn.execute(
        "SELECT count(*) AS n FROM app.tag_edges WHERE from_tag_id = %s OR to_tag_id = %s",
        (left, left),
    ).fetchone()["n"]
    assert remaining == 0


def test_t2s_views_filter_edges_by_user(conn, base, t2s_conn):
    alice = base["users"]["alice"]
    bob = base["users"]["bob"]
    a1 = _tag(conn, alice, "事务隔离")
    a2 = _tag(conn, alice, "隔离级别")
    b1 = _tag(conn, bob, "进程")
    b2 = _tag(conn, bob, "线程")
    _edge(conn, a1, a2)
    _edge(conn, b1, b2)

    t2s_conn.execute("SELECT set_config('app.user_id', %s, true)", (str(alice),))
    rows = t2s_conn.execute(
        "SELECT prerequisite_tag, tag_name FROM t2s.v_my_tag_edges ORDER BY edge_id"
    ).fetchall()
    assert rows == [{"prerequisite_tag": "事务隔离", "tag_name": "隔离级别"}]

    closure = t2s_conn.execute(
        "SELECT tag_name, prerequisite_tag, depth FROM t2s.v_my_tag_prerequisites"
    ).fetchall()
    assert closure == [{"tag_name": "隔离级别", "prerequisite_tag": "事务隔离", "depth": 1}]
    t2s_conn.rollback()

    t2s_conn.execute("SELECT set_config('app.user_id', %s, true)", (str(bob),))
    rows = t2s_conn.execute("SELECT tag_name FROM t2s.v_my_tag_edges").fetchall()
    assert rows == [{"tag_name": "线程"}]
    t2s_conn.rollback()
