"""已出版稿件读取接口的边界：登录才能读，且只出已出版内容。

文档是账号数据。这里锁住两件事：
1. 未登录（访客模式）拿不到任何人的稿件，接口直接 401；
2. 查询里必须带 status='published' 与作者 active，草稿不能漏出去。
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.api.deps import get_authenticated_user
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


def test_public_detail_requires_login(client: TestClient) -> None:
    """访客模式没有账号，任何人的稿件都不返回；401 比空列表更明确。"""
    session = RecordingSession()

    async def db():
        yield session

    client.app.dependency_overrides[get_db_session] = db
    try:
        response = client.get(f"/api/v1/public/docs/{uuid.uuid4()}", headers={})
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 401
    # 鉴权在取数之前，连查询都不该发出
    assert session.statements == []


def test_public_detail_hides_non_published_articles(client: TestClient) -> None:
    session = RecordingSession(rows=[])

    async def db():
        yield session

    client.app.dependency_overrides[get_db_session] = db
    client.app.dependency_overrides[get_authenticated_user] = lambda: object()
    try:
        response = client.get(
            f"/api/v1/public/docs/{uuid.uuid4()}",
            headers={"Authorization": "Bearer test"},
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 404
    sql = _sql(session.statements)
    assert "doc_articles.status = 'published'" in sql
    assert "users.status = 'active'" in sql
