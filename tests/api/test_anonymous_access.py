"""匿名访客权限接口：未登录时路由守卫与侧边栏的判定依据。

后端把数据库里 ``anonymous`` 角色的页面浏览码（``*.access``）与侧边栏显隐码
（``sidebar.*``）下发，前端未登录时据此决定放行/跳登录与入口显隐。这里覆盖三种
状态：角色不存在、角色停用、角色启用且有权限（只透出这两类展示码）。
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


def test_anonymous_access_filters_to_display_permissions(client: TestClient) -> None:
    # 只下发页面浏览码与侧边栏显隐码；chat.write / user.read 这类功能码不透出。
    session = _FakeSession(
        role=_role("active"),
        perm_rows=[
            "chat.access",
            "flow.access",
            "sidebar.show",
            "sidebar.entry.chat",
            "sidebar.entry.admin",
            "chat.write",
            "user.read",
        ],
    )
    response = _get(client, session)

    assert response.status_code == 200
    body = response.json()
    assert body["active"] is True
    assert body["role_name"] == "匿名用户"
    # 侧边栏入口必须随快照下发：后台给 anonymous 取消勾选后，游客端侧边栏
    # 要能跟着收起，否则「权限分配」对未登录访客失效。
    assert body["permissions"] == [
        "chat.access",
        "flow.access",
        "sidebar.entry.admin",
        "sidebar.entry.chat",
        "sidebar.show",
    ]


def test_anonymous_access_does_not_require_login(client: TestClient) -> None:
    """未登录访问是正常路径：不能是 401，否则游客永远读不到自己的准入依据。"""
    response = _get(client, _FakeSession(role=None))
    assert response.status_code != 401
