"""公开文档接口的边界：只出已出版内容。

这里不断言具体 SQL 全文，只断言真正要紧的一件事：查询里带上了
status='published' 与作者 active 的过滤（否则草稿会漏出去）。
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.core.database.deps import get_db_session


class _Result:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class RecordingSession:
    """记录收到的语句，并按预设返回标量 / 行。"""

    def __init__(self, *, scalar: object = 0, rows: list[object] | None = None) -> None:
        self.statements: list[object] = []
        self._scalar = scalar
        self._rows = rows or []

    async def scalar(self, statement: object):
        self.statements.append(statement)
        return self._scalar

    async def scalars(self, statement: object):
        self.statements.append(statement)
        return []

    async def execute(self, statement: object) -> _Result:
        self.statements.append(statement)
        return _Result(self._rows)


def _sql(statements: list[object]) -> str:
    return "\n".join(
        str(stmt.compile(compile_kwargs={"literal_binds": True})) for stmt in statements
    )


def test_public_list_filters_to_published_and_active_authors(client: TestClient) -> None:
    session = RecordingSession()

    async def db():
        yield session

    client.app.dependency_overrides[get_db_session] = db
    try:
        response = client.get("/api/v1/public/docs")
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["items"] == []
    sql = _sql(session.statements)
    # 草稿绝不能出现在公开列表里
    assert "doc_articles.status = 'published'" in sql
    # 已停用作者的内容不再对外展示
    assert "users.status = 'active'" in sql


def test_public_detail_hides_non_published_articles(client: TestClient) -> None:
    session = RecordingSession(rows=[])

    async def db():
        yield session

    client.app.dependency_overrides[get_db_session] = db
    try:
        response = client.get(f"/api/v1/public/docs/{uuid.uuid4()}")
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 404
    sql = _sql(session.statements)
    assert "doc_articles.status = 'published'" in sql
    assert "users.status = 'active'" in sql


def test_public_endpoints_do_not_require_login(client: TestClient) -> None:
    """未登录访问是正常路径：不能是 401，否则游客永远走不到自己的分支。"""
    session = RecordingSession()

    async def db():
        yield session

    client.app.dependency_overrides[get_db_session] = db
    try:
        response = client.get("/api/v1/public/docs", headers={})
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code != 401
