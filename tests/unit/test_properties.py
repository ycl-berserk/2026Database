"""属性测试：用 Hypothesis 随机生成输入，验证导入器与 SQL 守卫的不变量。"""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.importers import json_importer, markdown_importer, plain_text_importer
from app.importers.base import PLATFORMS, SENDERS, ImportResult
from app.services.text2sql_service import (
    VIEWS,
    GuardError,
    check_sql,
    wrap_sql,
)
from app.services.text2sql_service import (
    settings as t2s_settings,
)

JSON_VALUES = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=20),
    lambda children: (
        st.lists(children, max_size=3)
        | st.dictionaries(st.text(max_size=10), children, max_size=3)
    ),
    max_leaves=15,
)

TEXT = st.text(max_size=2000)

SENDERS_VALID = set(SENDERS.values())


def _assert_result_shape(result: ImportResult) -> None:
    assert isinstance(result, ImportResult)
    assert result.platform == result.platform.strip().lower()
    assert len(result.account_label) <= 100
    if not result.conversations:
        return
    assert result.account_label
    seen_ids: set[str] = set()
    for conv in result.conversations:
        assert isinstance(conv["title"], str)
        assert isinstance(conv["external_id"], str)
        assert len(conv["tags"]) <= 20
        assert all(0 < len(tag) <= 40 for tag in conv["tags"])
        assert conv["messages"]
        for seq, message in enumerate(conv["messages"], start=1):
            assert message["seq_no"] == seq
            assert message["sender"] is None or message["sender"] in SENDERS_VALID
            assert isinstance(message["content"], str) and message["content"]
            assert message["content"] == message["content"].strip()
    if result.ok:
        assert result.platform in PLATFORMS
        for conv in result.conversations:
            assert 0 < len(conv["title"]) <= 255
            assert 0 < len(conv["external_id"]) <= 200
            assert conv["external_id"] not in seen_ids
            seen_ids.add(conv["external_id"])
            assert all(message["sender"] in SENDERS_VALID for message in conv["messages"])


@settings(max_examples=200, deadline=None)
@given(JSON_VALUES)
def test_json_importer_accepts_arbitrary_json(doc) -> None:
    raw = json.dumps(doc, ensure_ascii=False).encode()
    _assert_result_shape(json_importer.parse(raw))


@settings(max_examples=200, deadline=None)
@given(st.binary(max_size=2000))
def test_importers_accept_arbitrary_bytes(raw: bytes) -> None:
    for parse in (json_importer.parse, markdown_importer.parse, plain_text_importer.parse):
        result = parse(raw)
        assert isinstance(result, ImportResult)
        _assert_result_shape(result)


@settings(max_examples=150, deadline=None)
@given(TEXT)
def test_text_importers_accept_arbitrary_text(text: str) -> None:
    raw = text.encode("utf-8")
    for parse in (markdown_importer.parse, plain_text_importer.parse):
        result = parse(raw)
        assert isinstance(result, ImportResult)
        _assert_result_shape(result)


DANGEROUS_KEYWORDS = (
    "DROP TABLE app.users",
    "DELETE FROM app.users",
    "INSERT INTO app.users (username) VALUES ('x')",
    "UPDATE app.users SET system_role = 'admin'",
    "CREATE TABLE app.evil (id int)",
    "ALTER TABLE app.users ADD COLUMN evil int",
    "TRUNCATE app.users",
    "GRANT ALL ON app.users TO PUBLIC",
    "COPY app.users TO '/tmp/leak.csv'",
    "MERGE INTO app.users USING app.users u ON true WHEN MATCHED THEN DELETE",
    "SET ROLE postgres",
    "DO $$ BEGIN PERFORM 1; END $$",
)


@pytest.mark.parametrize("fragment", DANGEROUS_KEYWORDS)
def test_dangerous_statements_are_rejected(fragment: str) -> None:
    with pytest.raises(GuardError):
        check_sql(fragment)
    with pytest.raises(GuardError):
        check_sql(f"SELECT 1; {fragment}")


FORBIDDEN_SOURCES = (
    "app.users",
    "public.users",
    "pg_catalog.pg_user",
    "information_schema.tables",
    "t2s.pg_class",
)


@pytest.mark.parametrize("target", FORBIDDEN_SOURCES)
def test_forbidden_sources_are_rejected(target: str) -> None:
    with pytest.raises(GuardError):
        check_sql(f"SELECT * FROM {target}")


@pytest.mark.parametrize(
    "fragment",
    [
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT current_setting('data_directory')",
        "SELECT * FROM dblink('host=evil', 'SELECT 1') AS t(x int)",
        "SELECT lo_import('/etc/passwd')",
    ],
)
def test_forbidden_functions_are_rejected(fragment: str) -> None:
    with pytest.raises(GuardError):
        check_sql(fragment)


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=200))
def test_guard_either_rejects_or_returns_stable_sql(text: str) -> None:
    try:
        cleaned = check_sql(text)
    except GuardError:
        return
    assert cleaned == cleaned.strip()
    assert not cleaned.endswith(";")
    assert "--" not in cleaned and "/*" not in cleaned
    assert check_sql(cleaned) == cleaned


@settings(max_examples=100, deadline=None)
@given(
    st.sampled_from(VIEWS),
    st.sampled_from(("*", "count(*)", "title", "code, name", "conversation_id")),
    st.booleans(),
)
def test_allowed_queries_are_accepted_and_idempotent(view: str, projection: str, semicolon: bool) -> None:
    sql = f"SELECT {projection} FROM {view}"
    if semicolon:
        sql = f"  {sql};  "
    cleaned = check_sql(sql)
    assert cleaned == f"SELECT {projection} FROM {view}"
    assert check_sql(cleaned) == cleaned


LITERAL_ALPHABET = st.characters(
    blacklist_categories=("Cs", "Cc"),
    blacklist_characters="'-/*;\\",
)


@settings(max_examples=100, deadline=None)
@given(st.text(alphabet=LITERAL_ALPHABET, max_size=40))
def test_masked_keywords_inside_literals_are_accepted(payload: str) -> None:
    sql = f"SELECT '{payload}' AS note FROM v_my_courses"
    assert check_sql(sql) == sql


@settings(max_examples=100, deadline=None)
@given(st.sampled_from(VIEWS))
def test_wrap_sql_keeps_query_and_appends_row_cap(view: str) -> None:
    sql = check_sql(f"SELECT * FROM {view}")
    wrapped = wrap_sql(sql)
    assert sql in wrapped
    assert wrapped.startswith("SELECT * FROM (")
    assert wrapped.endswith(f"LIMIT {t2s_settings.text2sql_row_limit + 1}")
