from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.api.deps import get_authenticated_user
from app.core.database.deps import get_db_session
from app.models.tables import User


async def _empty_db():
    yield object()


def test_chat_run_detail_requires_bearer(client: TestClient) -> None:
    client.app.dependency_overrides[get_db_session] = _empty_db
    try:
        response = client.get("/api/v1/me/chat-runs/run-aaaa")
    finally:
        client.app.dependency_overrides.clear()
    assert response.status_code == 401
    assert "run_id" not in response.json()


def test_chat_run_detail_hides_unknown_run_from_authenticated_user(client: TestClient) -> None:
    user = User(id=uuid.uuid4(), email="owner@example.com", username="owner")

    class Empty:
        async def scalars(self, _statement):
            return []

    async def db():
        yield Empty()

    async def current_user():
        return user

    client.app.dependency_overrides[get_db_session] = db
    client.app.dependency_overrides[get_authenticated_user] = current_user
    try:
        missing = client.get("/api/v1/me/chat-runs/run-aaaa", headers={"Authorization": "Bearer test-token-value"})
        other = client.get("/api/v1/me/chat-runs/run-bbbb", headers={"Authorization": "Bearer test-token-value"})
    finally:
        client.app.dependency_overrides.clear()
    assert missing.status_code == 404
    assert other.status_code == 404
    assert "usage" not in missing.json()
    assert "usage" not in other.json()
