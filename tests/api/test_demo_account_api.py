"""随机演示账号接口：只允许开发环境开放，角色限定 member / developer。

登录页图标彩蛋依赖它，所以「生产环境必须拒绝」是硬约束——否则线上会多一个
免邮箱验证的建号入口。用例里一律替换数据库会话：本套件不连真库。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.database.deps import get_db_session


class _EmptySession:
    """角色查不到：用来验证「环境开放但角色未初始化」的兜底文案。"""

    async def scalar(self, statement: object):
        return None

    async def flush(self) -> None:
        return None

    def add(self, obj: object) -> None:
        return None


def _post(client: TestClient, payload: dict):
    async def db():
        yield _EmptySession()

    client.app.dependency_overrides[get_db_session] = db
    try:
        return client.post("/api/v1/auth/demo-account", json=payload)
    finally:
        client.app.dependency_overrides.clear()


def test_demo_account_rejected_in_production(client: TestClient) -> None:
    original = client.app.state.settings
    client.app.state.settings = original.model_copy(update={"runtime_env": "production"})
    try:
        response = _post(client, {"role": "member"})
    finally:
        client.app.state.settings = original

    assert response.status_code == 501
    assert response.json()["error"]["code"] == "FEATURE_NOT_READY"


def test_demo_account_role_is_whitelisted(client: TestClient) -> None:
    # 只允许 member / developer：不能借彩蛋接口拿到 admin 或 super_admin。
    response = _post(client, {"role": "super_admin"})
    assert response.status_code == 422


def test_demo_account_requires_initialized_role(client: TestClient) -> None:
    response = _post(client, {"role": "developer"})

    assert response.status_code == 501
    assert response.json()["error"]["code"] == "FEATURE_NOT_READY"
