"""健康检查、模型列表和 OpenAPI。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.schemas.chat import (
    CHAT_COMPLETION_EXAMPLE,
    CHAT_COMPLETION_FULL_EXAMPLE,
    JSON_COMPLETION_EXAMPLE,
    STREAM_CHAT_EXAMPLE,
    STREAM_CHAT_FULL_EXAMPLE,
    TOOL_CALL_EXAMPLE,
    TOOL_CALL_FULL_EXAMPLE,
    ChatCompletionRequest,
    ChatCompletionResponse,
    StreamChatRequest,
    ToolCallRequest,
)
from tests.fakes import FakeProvider


def test_health_ready_and_models_do_not_call_provider(client: TestClient, provider: FakeProvider) -> None:
    health = client.get("/health")
    ready = client.get("/ready")
    models = client.get("/api/v1/models")
    root = client.get("/")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.json()["status"] == "ready"
    assert models.json()["data"] == [
        {"id": "deepseek-flash", "provider": "deepseek"},
        {"id": "deepseek-v4-pro", "provider": "deepseek"},
    ]
    assert root.json()["docs"] == "/docs"
    assert provider.calls == []


def test_openapi_and_docs(client: TestClient) -> None:
    docs = client.get("/docs")
    schema = client.get("/openapi.json")
    assert docs.status_code == 200
    assert "swagger" in docs.text.lower()
    assert schema.status_code == 200
    paths = schema.json()["paths"]
    assert "/api/v1/chat/completions" in paths
    assert "/api/v1/chat/completions/stream" in paths
    assert "/api/v1/chat/tool-calls" in paths
    assert "/api/v1/models" in paths
    assert "/health" in paths
    assert "/ready" in paths
    body = schema.json()
    description = body["info"]["description"]
    assert "Harness" in description
    stream_post = body["paths"]["/api/v1/chat/completions/stream"]["post"]
    stream = stream_post["description"]
    assert "event: error" in stream
    assert "event: done" in stream
    assert "流开始前" in stream
    assert "流开始后" in stream
    assert "断开" in stream
    assert body["info"]["version"] == "0.1.0"
    assert set(stream_post["responses"]) == {"200", "400", "404", "422", "500"}
    components = body["components"]["schemas"]
    assert set(components["ChatCompletionRequest"]["properties"]) == set(ChatCompletionRequest.model_fields)
    assert set(components["StreamChatRequest"]["properties"]) == set(StreamChatRequest.model_fields)
    assert set(components["ToolCallRequest"]["properties"]) == set(ToolCallRequest.model_fields)
    assert set(components["ChatCompletionResponse"]["properties"]) == set(ChatCompletionResponse.model_fields)
    assert "example" not in components["ChatCompletionRequest"]
    assert "example" not in components["StreamChatRequest"]
    assert "example" not in components["ToolCallRequest"]
    _assert_ready_example(
        body,
        "/api/v1/chat/completions",
        "full",
        CHAT_COMPLETION_FULL_EXAMPLE,
        ChatCompletionRequest,
    )
    _assert_ready_example(
        body,
        "/api/v1/chat/completions",
        "chat",
        CHAT_COMPLETION_EXAMPLE,
        ChatCompletionRequest,
    )
    _assert_ready_example(
        body,
        "/api/v1/chat/completions",
        "json",
        JSON_COMPLETION_EXAMPLE,
        ChatCompletionRequest,
    )
    _assert_ready_example(
        body,
        "/api/v1/chat/completions/stream",
        "full",
        STREAM_CHAT_FULL_EXAMPLE,
        StreamChatRequest,
    )
    _assert_ready_example(
        body,
        "/api/v1/chat/completions/stream",
        "stream",
        STREAM_CHAT_EXAMPLE,
        StreamChatRequest,
    )
    _assert_ready_example(
        body,
        "/api/v1/chat/tool-calls",
        "full",
        TOOL_CALL_FULL_EXAMPLE,
        ToolCallRequest,
    )
    _assert_ready_example(
        body,
        "/api/v1/chat/tool-calls",
        "calculator",
        TOOL_CALL_EXAMPLE,
        ToolCallRequest,
    )
    full = body["paths"]["/api/v1/chat/completions"]["post"]["requestBody"]["content"]["application/json"]
    assert next(iter(full["examples"])) == "full"


def _assert_ready_example(spec: dict, path: str, name: str, example: dict, model: type) -> None:
    content = spec["paths"][path]["post"]["requestBody"]["content"]["application/json"]
    value = content["examples"][name]["value"]
    assert value == example
    assert value["messages"][0]["content"] not in {"", "string"}
    assert "string" not in value.get("model", "")
    model.model_validate(value)
