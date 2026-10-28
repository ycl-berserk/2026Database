from __future__ import annotations

import io
import json
import zipfile

from app.services.backup_service import export_user_archive


def _unzip(payload: bytes) -> tuple[list[str], zipfile.ZipFile]:
    archive = zipfile.ZipFile(io.BytesIO(payload))
    return archive.namelist(), archive


def test_user_archive_contains_own_conversations_annotations_and_reviews(conn, base):
    user = {
        "id": base["users"]["alice"],
        "username": "alice",
        "display_name": "林晓",
    }
    filename, payload = export_user_archive(conn, user)
    assert filename.startswith("learning-space-all-alice-")
    assert filename.endswith(".zip")

    names, archive = _unzip(payload)
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["user"]["username"] == "alice"
    assert manifest["counts"] == {
        "conversations": 3,
        "annotations": 1,
        "reviews": 1,
    }
    assert "演示数据不含真实个人信息" in manifest["notice"]

    markdown = [name for name in names if name.startswith("conversations/")]
    assert len(markdown) == 3
    body = archive.read(markdown[0]).decode("utf-8")
    assert "演示数据不含真实个人信息" in body

    annotations = archive.read("annotations.csv").decode("utf-8-sig")
    assert "隔离级别要能对比记忆" in annotations
    reviews = archive.read("reviews.csv").decode("utf-8-sig")
    assert "pending" in reviews


def test_user_archive_excludes_other_members_conversations(conn, base):
    bob = {"id": base["users"]["bob"], "username": "bob", "display_name": "周博"}
    _, payload = export_user_archive(conn, bob)

    names, archive = _unzip(payload)
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["counts"] == {
        "conversations": 0,
        "annotations": 0,
        "reviews": 0,
    }
    assert not [name for name in names if name.startswith("conversations/")]
    assert "事务" not in archive.read("annotations.csv").decode("utf-8-sig")
