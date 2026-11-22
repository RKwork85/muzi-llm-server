"""匿名访客页面浏览权限接口：未登录时路由守卫的准入依据。

后端把数据库里 ``anonymous`` 角色的页面浏览权限（``*.access``）下发，
前端路由守卫在「未检测到角色信息」时据此判定放行或跳登录。这里覆盖三种
状态：角色不存在、角色停用、角色启用且有权限（只透出页面浏览码）。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.database.deps import get_db_session


class _FakeSession:
    """只返回预设值，不真正执行 SQL：endpoint 只读 role 行与权限码。"""

    def __init__(self, *, role: object = None, perm_rows: list[str] | None = None) -> None:
        self.role = role
        self.perm_rows = list(perm_rows or [])

    async def scalar(self, statement: object):
        return self.role

    async def scalars(self, statement: object):
        return self.perm_rows

    async def execute(self, statement: object):
        return _Result([])


class _Result:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


def _get(client: TestClient, session: _FakeSession):
    async def db():
        yield session

    client.app.dependency_overrides[get_db_session] = db
    try:
        return client.get("/api/v1/public/anonymous-access")
    finally:
        client.app.dependency_overrides.clear()


def _role(status: str) -> SimpleNamespace:
    return SimpleNamespace(code="anonymous", name="匿名用户", status=status, id=uuid.uuid4())


def test_anonymous_access_when_role_missing(client: TestClient) -> None:
    response = _get(client, _FakeSession(role=None))

    assert response.status_code == 200
    body = response.json()
    assert body["active"] is False
    assert body["permissions"] == []


def test_anonymous_access_when_role_disabled(client: TestClient) -> None:
    response = _get(client, _FakeSession(role=_role("disabled")))

    assert response.status_code == 200
    body = response.json()
    assert body["active"] is False
    assert body["role_name"] == "匿名用户"
    assert body["permissions"] == []


def test_anonymous_access_filters_to_page_permissions(client: TestClient) -> None:
    # chat.write / user.read 不是页面浏览码，不应透出给匿名访客。
    session = _FakeSession(
        role=_role("active"),
        perm_rows=["chat.access", "flow.access", "chat.write", "user.read"],
    )
    response = _get(client, session)

    assert response.status_code == 200
    body = response.json()
    assert body["active"] is True
    assert body["role_name"] == "匿名用户"
    assert body["permissions"] == ["chat.access", "flow.access"]


def test_anonymous_access_does_not_require_login(client: TestClient) -> None:
    """未登录访问是正常路径：不能是 401，否则游客永远读不到自己的准入依据。"""
    response = _get(client, _FakeSession(role=None))
    assert response.status_code != 401
