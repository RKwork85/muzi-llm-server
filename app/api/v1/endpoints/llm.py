"""画布「文字编辑节点」的模型补全接口。

前端只描述「哪个板块、用哪个模型、要写什么」，密钥与通道选择都留在服务端：

- ``deepseek``：走应用生命周期内复用的 ``AsyncOpenAI`` 客户端（``deepseek-flash`` 默认）；
- ``ark``：经 httpx 调火山方舟 ``/chat/completions``（默认 ``Doubao Seed 2.1 Lite``）。

两个通道的响应都被归一成 ``content + usage + elapsed_ms``，
节点据此展示本次 token 消耗与执行时间，不涉及画布结构或会话存储。

``/completions/stream`` 是同一份能力的 SSE 版本：节点卡片按对话页的方式边收边渲染，
事件顺序固定为 ``delta* → usage? → done``，中途失败只发一条 ``error``。
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.deps import get_authenticated_user, get_stream_user_id
from app.core.sse import format_sse
from app.core.exceptions import (
    AppError,
    ConfigurationError,
    InvalidModelResponseError,
    ModelNotAllowedError,
    UpstreamAuthError,
    UpstreamError,
    UpstreamRateLimitError,
    UpstreamTimeoutError,
)
from app.models.tables import User
from app.providers.base import ProviderChatRequest, UsageStats
from app.schemas.llm import (
    TextCompletionRequest,
    TextCompletionResponse,
    TextCompletionUsage,
)

router = APIRouter(prefix="/llm", tags=["llm"])

AuthenticatedUser = Annotated[User, Depends(get_authenticated_user)]

# 方舟文本模型白名单：只开放豆包 Seed 2.x 的文本型号，避免把图片 / 视频型号传进对话接口。
ARK_TEXT_MODELS = (
    "doubao-seed-2-1-lite-260915",
    "doubao-seed-2-1-pro-260915",
    "doubao-seed-2-1-turbo-260628",
)
ARK_DEFAULT_TEXT_MODEL = ARK_TEXT_MODELS[0]

# 上游错误正文可能很长，只把能定位问题的头部透给前端。
_DETAIL_LIMIT = 300


def _settings(request: Request):
    return getattr(request.app.state, "settings", None)


def _deepseek_model(settings, requested: str) -> str:
    """空值用默认模型；显式模型必须在配置的 allowlist 里。"""
    model = (requested or "").strip() or settings.deepseek_model
    if model not in settings.model_allowlist():
        raise ModelNotAllowedError(f"DeepSeek 模型不在允许列表中：{model}")
    return model


def _ark_model(requested: str) -> str:
    model = (requested or "").strip() or ARK_DEFAULT_TEXT_MODEL
    if model not in ARK_TEXT_MODELS:
        raise ModelNotAllowedError(f"方舟文本模型不在允许列表中：{model}")
    return model


def _usage_out(prompt_tokens: int, completion_tokens: int, total_tokens: int) -> TextCompletionUsage:
    total = total_tokens if total_tokens > 0 else prompt_tokens + completion_tokens
    return TextCompletionUsage(
        prompt_tokens=max(0, prompt_tokens),
        completion_tokens=max(0, completion_tokens),
        total_tokens=max(0, total),
    )


def _usage_from_stats(usage: UsageStats | None) -> TextCompletionUsage:
    if usage is None:
        return _usage_out(0, 0, 0)
    return _usage_out(usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)


def _usage_from_ark(raw: Any) -> TextCompletionUsage:
    usage = raw if isinstance(raw, dict) else {}
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total = int(usage.get("total_tokens") or 0)
    return _usage_out(prompt, completion, total)


def _ark_message(status: int, text: str) -> str:
    """从方舟错误正文里抠出可读的一句话。"""
    body: Any = None
    if text:
        try:
            body = json.loads(text)
        except ValueError:
            body = None
    message = ""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "")
        elif isinstance(error, str):
            message = error
        message = message or str(body.get("message") or "")
    return " ".join(message.split())[:_DETAIL_LIMIT]


def _ark_error_from_text(status: int, text: str) -> Exception:
    """状态码 + 错误正文 → 项目统一的业务异常（流式响应读完正文后同样适用）。"""
    message = _ark_message(status, text)
    if status in (401, 403):
        return UpstreamAuthError(message or "方舟密钥无效或无权访问该模型")
    if status == 429:
        return UpstreamRateLimitError(message or "方舟请求过于频繁，请稍后重试")
    if status >= 500:
        return UpstreamError(message or "方舟服务暂时不可用")
    return UpstreamError(message or f"方舟请求失败（HTTP {status}）")


def _ark_error(response: httpx.Response) -> Exception:
    """把方舟的 HTTP 错误映射成项目统一的业务异常。"""
    return _ark_error_from_text(response.status_code, response.text)


def _wants_thinking_disabled(status: int, text: str) -> bool:
    """方舟旧型号不认 ``thinking`` 字段：只对这类 400 做一次降级重试。"""
    if status != 400:
        return False
    return "thinking" in _ark_message(status, text).lower()


def _ark_key(settings) -> str:
    return str(getattr(settings, "ark_api_key", "") or "").strip()


def _ark_endpoint(settings) -> str:
    return f"{str(settings.ark_base_url).rstrip('/')}/chat/completions"


def _ark_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _ark_payload(body: TextCompletionRequest, model: str, *, stream: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [item.model_dump() for item in body.messages],
        "stream": stream,
    }
    if body.temperature is not None:
        payload["temperature"] = body.temperature
    if body.max_tokens is not None:
        payload["max_tokens"] = body.max_tokens
    if stream:
        # 不要求 include_usage 时，豆包最后一个分片不带 usage，节点就显示不出 token 消耗。
        payload["stream_options"] = {"include_usage": True}
    return payload


async def _complete_ark(settings, body: TextCompletionRequest, model: str) -> TextCompletionResponse:
    api_key = _ark_key(settings)
    if not api_key:
        raise ConfigurationError("方舟密钥未配置")

    payload = _ark_payload(body, model, stream=False)
    url = _ark_endpoint(settings)
    headers = _ark_headers(api_key)
    try:
        async with httpx.AsyncClient(timeout=settings.ark_timeout_seconds) as client:
            # 文字编辑只要成品：关掉思考，否则豆包会先花几千 reasoning tokens，
            # 一次改写能拖到半分钟，节点上的执行时间也会失真。
            response = await client.post(
                url, headers=headers, json={**payload, "thinking": {"type": "disabled"}}
            )
            if _wants_thinking_disabled(response.status_code, response.text):
                response = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise UpstreamTimeoutError("方舟生成超时，请缩短素材或稍后重试") from exc
    except httpx.HTTPError as exc:
        raise UpstreamError("方舟连接失败，请稍后重试") from exc

    if response.status_code >= 400:
        raise _ark_error(response)

    try:
        data = response.json()
    except ValueError as exc:
        raise InvalidModelResponseError("方舟返回了无法解析的响应") from exc
    if not isinstance(data, dict):
        raise InvalidModelResponseError("方舟返回了无法解析的响应")

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise InvalidModelResponseError("方舟没有返回正文")
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    content = message.get("content")
    if not isinstance(content, str):
        content = ""

    return TextCompletionResponse(
        id=str(data.get("id") or ""),
        provider="ark",
        model=str(data.get("model") or model),
        content=content,
        finish_reason=str(first.get("finish_reason") or "stop"),
        usage=_usage_from_ark(data.get("usage")),
    )


async def _complete_deepseek(request: Request, body: TextCompletionRequest, model: str) -> TextCompletionResponse:
    provider = getattr(request.app.state, "provider", None)
    if provider is None:
        raise ConfigurationError()

    result = await provider.create_chat_completion(
        ProviderChatRequest(
            model=model,
            messages=[item.model_dump() for item in body.messages],
            temperature=body.temperature,
            max_tokens=body.max_tokens,
        )
    )
    return TextCompletionResponse(
        id=str(getattr(result, "id", "") or ""),
        provider="deepseek",
        model=str(getattr(result, "model", "") or model),
        content=str(getattr(result, "content", "") or ""),
        finish_reason=str(getattr(result, "finish_reason", "") or "stop"),
        usage=_usage_from_stats(getattr(result, "usage", None)),
    )


@router.post(
    "/completions",
    response_model=TextCompletionResponse,
    summary="画布文字编辑节点的非流式补全",
)
async def complete_text(
    body: TextCompletionRequest,
    request: Request,
    user: AuthenticatedUser,
) -> TextCompletionResponse:
    """一次请求生成一段文本，并回传 token 用量与服务端耗时。

    登录用户可用；画布板块本身要求登录，这里再做一次鉴权以免接口被匿名刷调用。
    ``user`` 暂时只用于鉴权，额度结算仍沿用画布既有的「只展示不扣减」策略。
    """
    del user  # 仅用于登录校验
    settings = _settings(request)
    if settings is None:
        raise ConfigurationError()

    started = time.perf_counter()
    if body.provider == "ark":
        model = _ark_model(body.model)
        result = await _complete_ark(settings, body, model)
    else:
        model = _deepseek_model(settings, body.model)
        result = await _complete_deepseek(request, body, model)

    result.elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
    return result


# 上面是非流式版本，下面是同一份能力的 SSE 版本：事件顺序固定为 delta* → usage? → done。

async def _iter_deepseek_stream(
    request: Request, body: TextCompletionRequest, model: str
) -> AsyncIterator[tuple[str, Any]]:
    provider = getattr(request.app.state, "provider", None)
    if provider is None:
        raise ConfigurationError()

    stream = provider.stream_chat_completion(
        ProviderChatRequest(
            model=model,
            messages=[item.model_dump() for item in body.messages],
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            # 与方舟一致：文字编辑只要成品，不要先吐一段思考。
            thinking={"type": "disabled"},
        )
    )
    async for chunk in stream:
        content = getattr(chunk, "content", None)
        if isinstance(content, str) and content:
            yield "delta", content
        usage = getattr(chunk, "usage", None)
        if isinstance(usage, UsageStats):
            yield "usage", _usage_from_stats(usage)
        finish = getattr(chunk, "finish_reason", None)
        if isinstance(finish, str) and finish:
            yield "finish", finish


async def _open_ark_stream(client: httpx.AsyncClient, url: str, headers: dict[str, str], payload: dict[str, Any]):
    request = client.build_request("POST", url, headers=headers, json=payload)
    return await client.send(request, stream=True)


async def _iter_ark_stream(
    settings, body: TextCompletionRequest, model: str
) -> AsyncIterator[tuple[str, Any]]:
    api_key = _ark_key(settings)
    if not api_key:
        raise ConfigurationError("方舟密钥未配置")

    payload = _ark_payload(body, model, stream=True)
    url = _ark_endpoint(settings)
    headers = _ark_headers(api_key)
    try:
        async with httpx.AsyncClient(timeout=settings.ark_timeout_seconds) as client:
            response = await _open_ark_stream(
                client, url, headers, {**payload, "thinking": {"type": "disabled"}}
            )
            if response.status_code >= 400:
                await response.aread()
                text = response.text
                await response.aclose()
                if _wants_thinking_disabled(response.status_code, text):
                    response = await _open_ark_stream(client, url, headers, payload)
                else:
                    raise _ark_error_from_text(response.status_code, text)
            try:
                if response.status_code >= 400:
                    await response.aread()
                    raise _ark_error_from_text(response.status_code, response.text)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        data = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(data, dict):
                        continue
                    if isinstance(data.get("usage"), dict):
                        yield "usage", _usage_from_ark(data["usage"])
                    choices = data.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    first = choices[0] if isinstance(choices[0], dict) else {}
                    delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        yield "delta", content
                    finish = first.get("finish_reason")
                    if isinstance(finish, str) and finish:
                        yield "finish", finish
            finally:
                await response.aclose()
    except httpx.TimeoutException as exc:
        raise UpstreamTimeoutError("方舟生成超时，请缩短素材或稍后重试") from exc
    except httpx.HTTPError as exc:
        raise UpstreamError("方舟连接失败，请稍后重试") from exc


async def _stream_frames(
    request: Request, body: TextCompletionRequest, provider: str, model: str
) -> AsyncIterator[str]:
    """把两条通道统一成 delta* → usage? → done；任何失败都收敛成一条 error。"""
    started = time.perf_counter()
    usage = TextCompletionUsage()
    finish_reason = "stop"
    try:
        source = (
            _iter_ark_stream(_settings(request), body, model)
            if provider == "ark"
            else _iter_deepseek_stream(request, body, model)
        )
        async for kind, value in source:
            if kind == "delta":
                yield format_sse("delta", {"content": value})
            elif kind == "usage":
                usage = value
                yield format_sse("usage", {"usage": value.model_dump()})
            elif kind == "finish":
                finish_reason = str(value)
    except AppError as exc:
        yield format_sse("error", {"code": exc.code, "message": exc.message})
        return
    except Exception:  # noqa: BLE001 - 流已经开始了，只能把细节收进一条 error 帧
        yield format_sse("error", {"code": "UPSTREAM_ERROR", "message": "模型服务暂时不可用，请稍后重试"})
        return

    elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
    yield format_sse(
        "done",
        {
            "provider": provider,
            "model": model,
            "finish_reason": finish_reason,
            "elapsed_ms": elapsed_ms,
            "usage": usage.model_dump(),
        },
    )


@router.post(
    "/completions/stream",
    summary="画布文字编辑节点的流式补全（SSE）",
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": (
                "事件顺序：delta（正文分片）→ usage（有则发）→ done；"
                "流开始后的失败以 error 事件返回，HTTP 状态保持 200。"
            ),
        }
    },
)
async def stream_text(
    body: TextCompletionRequest,
    request: Request,
    user_id: Annotated[str, Depends(get_stream_user_id)],
) -> StreamingResponse:
    """与 ``/completions`` 同参数，但按 SSE 边生成边返回。

    模型白名单与密钥在开始推流之前校验：这类错误仍是普通 JSON 响应，
    前端可以直接按错误码提示，不需要先解析一条 SSE 流。

    鉴权刻意用 ``get_stream_user_id``：SSE 的响应在断开前不会结束，
    用请求级 session 会把连接池占满（见 docs/SSE_DEPLOYMENT.md）。
    """
    del user_id  # 仅用于登录校验
    settings = _settings(request)
    if settings is None:
        raise ConfigurationError()

    if body.provider == "ark":
        model = _ark_model(body.model)
        if not _ark_key(settings):
            raise ConfigurationError("方舟密钥未配置")
    else:
        model = _deepseek_model(settings, body.model)
        if getattr(request.app.state, "provider", None) is None:
            raise ConfigurationError()

    return StreamingResponse(
        _stream_frames(request, body, body.provider, model),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 关掉反向代理的缓冲，否则前端会「一次收到整段正文」，看起来不像流式。
            "X-Accel-Buffering": "no",
        },
    )
