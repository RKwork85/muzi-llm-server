"""画布文字编辑节点的补全接口。

覆盖三件事：
- DeepSeek / 方舟两条通道都能回传 ``content + usage + elapsed_ms``；
- 流式端点把两条通道归一成 ``delta* → usage? → done``；
- 模型不在白名单、方舟未配密钥时给出确定的业务错误码，而不是 500 大堆栈。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_authenticated_user, get_stream_user_id
from app.models.tables import User
from app.providers.base import StreamChunk, UsageStats
from tests.fakes import make_result

MESSAGES = [
    {"role": "system", "content": "你是小红书运营"},
    {"role": "user", "content": "【上游素材】\n防晒霜测评"},
]


def _login(client: TestClient) -> None:
    """画布接口要求登录，这里只替换鉴权依赖，不碰数据库。"""

    async def current_user() -> User:
        return User(id=uuid.uuid4(), email="writer@example.com", username="writer")

    client.app.dependency_overrides[get_authenticated_user] = current_user


def _post(client: TestClient, **overrides: Any):
    payload: dict[str, Any] = {"provider": "deepseek", "model": "", "messages": MESSAGES}
    payload.update(overrides)
    try:
        return client.post("/api/v1/llm/completions", json=payload)
    finally:
        client.app.dependency_overrides.clear()


def test_deepseek_returns_content_usage_and_elapsed(client: TestClient, provider) -> None:
    provider.push(make_result(content="这是一篇种草笔记", model="deepseek-flash"))
    _login(client)

    response = _post(client, model="", temperature=0.85, max_tokens=1600)

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "这是一篇种草笔记"
    assert body["provider"] == "deepseek"
    assert body["model"] == "deepseek-flash"
    assert body["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert body["elapsed_ms"] >= 0

    # 默认模型与参数确实透传给了 provider，节点侧的提示词组装才算真正接通。
    call = provider.calls[-1]
    assert call.model == "deepseek-flash"
    assert call.messages[0]["role"] == "system"
    assert call.temperature == 0.85
    assert call.max_tokens == 1600


def test_deepseek_rejects_model_outside_allowlist(client: TestClient, provider) -> None:
    _login(client)

    response = _post(client, model="gpt-9")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MODEL_NOT_ALLOWED"
    assert not provider.calls


def test_ark_requires_configured_key(client: TestClient) -> None:
    _login(client)

    response = _post(client, provider="ark", model="doubao-seed-2-1-lite-260915")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "CONFIGURATION_ERROR"


class _FakeArkResponse:
    def __init__(self, status_code: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._body = body or {
            "id": "chatcmpl-ark",
            "model": "doubao-seed-2-1-lite-260915",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "头条资讯稿正文"},
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        }
        # 端点会读 response.text 判断「是不是旧型号不认 thinking」
        self.text = json.dumps(self._body, ensure_ascii=False)

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeArkClient:
    """替掉 httpx.AsyncClient：只记录请求，不真的出网。"""

    last_request: dict[str, Any] = {}
    posts: list[dict[str, Any]] = []

    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "_FakeArkClient":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]):
        _FakeArkClient.last_request = {"url": url, "headers": headers, "json": json}
        _FakeArkClient.posts.append(json)
        return _FakeArkResponse()


def test_ark_returns_usage_from_upstream(
    client: TestClient, settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.ark_api_key = "ark-test-key"
    _FakeArkClient.posts = []
    monkeypatch.setattr("app.api.v1.endpoints.llm.httpx.AsyncClient", _FakeArkClient)
    _login(client)

    response = _post(client, provider="ark", model="")

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "头条资讯稿正文"
    assert body["provider"] == "ark"
    assert body["usage"] == {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}
    assert body["elapsed_ms"] >= 0

    request = _FakeArkClient.last_request
    assert request["url"].endswith("/chat/completions")
    assert request["headers"]["Authorization"] == "Bearer ark-test-key"
    # 前端留空模型时落到方舟默认的豆包 Lite
    assert request["json"]["model"] == "doubao-seed-2-1-lite-260915"
    # 文字编辑只要成品：默认关闭思考，避免 reasoning token 推高耗时与费用
    assert request["json"]["thinking"] == {"type": "disabled"}


class _RejectThinkingArkClient(_FakeArkClient):
    """旧型号不认 thinking 字段：第一次 400，去掉字段后重试成功。"""

    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]):
        _FakeArkClient.posts.append(json)
        if "thinking" in json:
            return _FakeArkResponse(
                status_code=400,
                body={"error": {"message": "Invalid parameter: thinking is not supported"}},
            )
        return _FakeArkResponse()


def test_ark_retries_without_thinking_when_model_rejects_it(
    client: TestClient, settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.ark_api_key = "ark-test-key"
    _FakeArkClient.posts = []
    monkeypatch.setattr("app.api.v1.endpoints.llm.httpx.AsyncClient", _RejectThinkingArkClient)
    _login(client)

    response = _post(client, provider="ark", model="doubao-seed-2-1-lite-260915")

    assert response.status_code == 200
    assert len(_FakeArkClient.posts) == 2
    assert "thinking" in _FakeArkClient.posts[0]
    assert "thinking" not in _FakeArkClient.posts[1]


# ---------------- 流式（SSE） ----------------


def _login_stream(client: TestClient) -> None:
    """流式端点用短事务鉴权依赖，只返回 user_id，不借请求级 session。"""

    async def current_user_id() -> str:
        return str(uuid.uuid4())

    client.app.dependency_overrides[get_stream_user_id] = current_user_id


def _post_stream(client: TestClient, **overrides: Any):
    payload: dict[str, Any] = {"provider": "deepseek", "model": "", "messages": MESSAGES}
    payload.update(overrides)
    try:
        return client.post("/api/v1/llm/completions/stream", json=payload)
    finally:
        client.app.dependency_overrides.clear()


def _events(text: str) -> list[tuple[str, dict[str, Any]]]:
    """把 SSE 正文字段解析成 [(event, data)]，顺序即断言依据。"""
    out: list[tuple[str, dict[str, Any]]] = []
    event = ""
    for line in text.splitlines():
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            out.append((event, json.loads(line[len("data:") :].strip())))
    return out


def test_deepseek_stream_emits_deltas_then_done(client: TestClient, provider) -> None:
    provider.push(
        [
            StreamChunk(content="平价"),
            StreamChunk(content="防晒"),
            StreamChunk(finish_reason="stop"),
            StreamChunk(usage=UsageStats(prompt_tokens=6, completion_tokens=2, total_tokens=8)),
        ]
    )
    _login_stream(client)

    response = _post_stream(client, model="deepseek-flash", temperature=0.8, max_tokens=1200)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    kinds = [kind for kind, _ in events]
    # 正文分片按顺序到达，用量与收尾帧不会插到中间
    assert kinds == ["delta", "delta", "usage", "done"]
    assert events[0][1]["content"] == "平价"
    assert events[1][1]["content"] == "防晒"
    assert events[2][1]["usage"] == {
        "prompt_tokens": 6,
        "completion_tokens": 2,
        "total_tokens": 8,
    }
    done = events[3][1]
    assert done["provider"] == "deepseek"
    assert done["model"] == "deepseek-flash"
    assert done["elapsed_ms"] >= 0

    call = provider.calls[-1]
    assert call.messages[1]["content"].startswith("【上游素材】")
    assert call.temperature == 0.8
    # 文字编辑要成品不要思考：流式同样显式关闭
    assert call.thinking == {"type": "disabled"}


class _FakeArkStreamResponse:
    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self.status_code = status_code
        self._lines = lines
        self.text = ""

    async def aread(self) -> bytes:
        return b""

    async def aclose(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeArkStreamClient:
    """替掉流式 httpx.AsyncClient：build_request + send，返回脚本化分片。"""

    requests: list[dict[str, Any]] = []

    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "_FakeArkStreamClient":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    def build_request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        return {"method": method, "url": url, "json": kwargs.get("json")}

    async def send(self, request: dict[str, Any], stream: bool = False):
        _FakeArkStreamClient.requests.append(request)
        return _FakeArkStreamResponse(
            [
                'data: {"choices":[{"delta":{"content":"油皮"}}]}',
                'data: {"choices":[{"delta":{"content":"亲妈"},"finish_reason":"stop"}]}',
                'data: {"choices":[],"usage":{"prompt_tokens":9,"completion_tokens":4,"total_tokens":13}}',
                "data: [DONE]",
            ]
        )


def test_ark_stream_parses_sse_lines(
    client: TestClient, settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.ark_api_key = "ark-test-key"
    _FakeArkStreamClient.requests = []
    monkeypatch.setattr("app.api.v1.endpoints.llm.httpx.AsyncClient", _FakeArkStreamClient)
    _login_stream(client)

    response = _post_stream(client, provider="ark", model="")

    assert response.status_code == 200
    events = _events(response.text)
    assert [kind for kind, _ in events] == ["delta", "delta", "usage", "done"]
    assert events[0][1]["content"] == "油皮"
    assert events[1][1]["content"] == "亲妈"
    assert events[2][1]["usage"] == {
        "prompt_tokens": 9,
        "completion_tokens": 4,
        "total_tokens": 13,
    }
    assert events[3][1]["finish_reason"] == "stop"

    payload = _FakeArkStreamClient.requests[0]["json"]
    assert payload["stream"] is True
    # 不要 usage 的话最后一个分片不带用量，节点就没法显示 token 消耗
    assert payload["stream_options"] == {"include_usage": True}
    assert payload["thinking"] == {"type": "disabled"}


def test_stream_rejects_model_outside_allowlist(client: TestClient, provider) -> None:
    _login_stream(client)

    response = _post_stream(client, model="gpt-9")

    # 推流前的校验仍是普通 JSON：前端不用先解析一条 SSE 流才知道模型不合法
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MODEL_NOT_ALLOWED"
    assert not provider.calls


def test_stream_reports_upstream_failure_as_error_frame(client: TestClient, provider) -> None:
    provider.push(RuntimeError("upstream exploded"))
    _login_stream(client)

    response = _post_stream(client)

    # 流已经开始了，HTTP 状态保持 200，失败只能体现为一条 error 事件
    assert response.status_code == 200
    events = _events(response.text)
    assert [kind for kind, _ in events] == ["error"]
    assert events[0][1]["code"] == "UPSTREAM_ERROR"
    # 上游原始异常不外泄给客户端
    assert "exploded" not in response.text
