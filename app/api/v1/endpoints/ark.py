"""方舟请求经服务端转发，密钥和额度都不交给浏览器。"""

from __future__ import annotations

import json
import re
import uuid
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import get_authenticated_user
from app.authorization.application.audit_service import QuotaService
from app.authorization.application.quota_bridge import settle_in_new_session
from app.core.context import get_request_id
from app.core.database.deps import DbSession
from app.core.exceptions import ArkFileNotFoundError, ConfigurationError, InvalidRequestError, UpstreamError
from app.models.tables import ArkFile, User

router = APIRouter(prefix="/ark", tags=["ark"])
_FILE_ID = r"^[A-Za-z0-9_-]{1,128}$"
_MAX_FILE_BYTES = 32 * 1024 * 1024

# 图片生成固定计费：每成功生成一张图扣固定 token（系统只认 token，人民币换算在充值/定价系统）。
# 暂定只有 Seedream 5.0 Flash 计费（0.12 元/张 → 150000 token/张），其他模型号暂未加入（计 0）。
# 环境变量 TOOL_PRICE_IMAGE_GENERATION_TOKENS 可覆盖单张 token。
IMAGE_TOOL_NAME = "image_generation"
IMAGE_DEFAULT_TOKENS = 150000
IMAGE_PRICED_MODELS = ("doubao-seedream-5-0-flash",)


def _settings(request: Request):
    settings = getattr(request.app.state, "settings", None)
    if settings is None or not str(getattr(settings, "ark_api_key", "")).strip():
        raise ConfigurationError("方舟密钥未配置")
    return settings


def _auth_headers(settings, *, json_body: bool) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {settings.ark_api_key.strip()}"}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def owns_ark_file(owner_id: uuid.UUID, requester_id: uuid.UUID) -> bool:
    return owner_id == requester_id


def _usage_amount(payload: dict[str, Any]) -> tuple[int, int, int]:
    """按响应里的 token 入账。图片接口常用 output_tokens，没有 token 数则为 0。"""
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    total = int(usage.get("total_tokens") or 0)
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    if total <= 0:
        total = prompt + completion
    return total, prompt, completion


def _successful_image_count(payload: dict[str, Any]) -> int:
    """成功生成的图片张数（跳过组图里带 error 的部分失败项）。"""
    data = payload.get("data") or []
    if not isinstance(data, list):
        return 0
    return sum(1 for item in data if isinstance(item, dict) and not item.get("error"))


def _image_is_priced(model: str | None) -> bool:
    """暂定只有列入 IMAGE_PRICED_MODELS 的图片模型计费，其余暂未加入。"""
    m = (model or "").strip().lower()
    return any(m.startswith(prefix) for prefix in IMAGE_PRICED_MODELS)


def _image_fixed_tokens(model: str | None, image_count: int) -> int:
    """图片生成固定计费额度：成功张数 × 每张固定 token（未定价模型为 0）。"""
    if image_count <= 0 or not _image_is_priced(model):
        return 0
    from app.metering.tool_pricing import quote_tool_tokens
    from app.tools.registry import ToolPricing

    pricing = ToolPricing(model="fixed_tokens", tokens=IMAGE_DEFAULT_TOKENS, unit="image")
    return quote_tool_tokens(pricing, tool_name=IMAGE_TOOL_NAME, success=True, units=image_count)


def _public_image_usage(
    payload: dict[str, Any],
    *,
    prompt: str = "",
    images: Any = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """图片生成按固定 token 计费：每成功生成一张图扣固定 token，系统只认 token。

    图片不是文本模型 token，无 input/output breakdown；用 tool_usage 表达
    「图片生成」的工具固定计费，前端据此显示「图片生成 N 张 × 固定 token」。
    """
    image_count = _successful_image_count(payload) if isinstance(payload, dict) else 0
    image_tokens = _image_fixed_tokens(str(payload.get("model") or ""), image_count) if isinstance(payload, dict) else 0
    public: dict[str, Any] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "tool_usage": [
            {
                "tool_name": IMAGE_TOOL_NAME,
                "calls": image_count,
                "tokens": 0,
                "priced_tokens": image_tokens,
                "unit": "image",
            }
        ]
        if image_count > 0
        else [],
    }
    raw = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    if raw.get("generated_images") is not None:
        public["generated_images"] = raw.get("generated_images")
    return public, None


@router.post("/images/generations", summary="经服务端生成图片并结算额度")
async def generate_images(
    request: Request,
    session: DbSession,
    user: Annotated[User, Depends(get_authenticated_user)],
) -> dict[str, Any]:
    settings = _settings(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise InvalidRequestError("图片请求不是合法 JSON") from exc
    if not isinstance(body, dict):
        raise InvalidRequestError("图片请求格式无效")
    conversation_id = body.pop("conversation_id", None)
    conversation_title = body.pop("conversation_title", None)
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        conversation_id = None
    if not isinstance(conversation_title, str) or not conversation_title.strip():
        conversation_title = None
    history_chat_id = body.pop("history_chat_id", None)
    if not isinstance(history_chat_id, str) or not history_chat_id.strip():
        history_chat_id = None
    if conversation_id is None and history_chat_id:
        conversation_id = f"chat:{history_chat_id.strip()[:64]}"
    model = body.get("model")
    prompt = body.get("prompt")
    if not isinstance(model, str) or not model.strip() or not isinstance(prompt, str) or not prompt.strip():
        raise InvalidRequestError("缺少图片模型或提示词")
    await QuotaService(session).authorize(user.id)
    await session.commit()
    url = f"{settings.ark_base_url.rstrip('/')}/images/generations"
    try:
        async with httpx.AsyncClient(timeout=settings.ark_timeout_seconds) as client:
            response = await client.post(url, headers=_auth_headers(settings, json_body=True), json=body)
    except httpx.HTTPError as exc:
        raise UpstreamError("方舟图片生成失败") from exc
    try:
        payload = response.json()
    except Exception as exc:
        raise UpstreamError("方舟图片响应无法解析") from exc
    if response.status_code >= 400:
        message = ""
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or "")
        raise UpstreamError(message or "方舟图片生成失败")
    image_count = _successful_image_count(payload) if isinstance(payload, dict) else 0
    extra = {"conversation_title": conversation_title[:200]} if conversation_title else None
    public_usage, _meter = _public_image_usage(
        payload if isinstance(payload, dict) else {},
        prompt=prompt if isinstance(prompt, str) else "",
        images=body.get("image"),
    )
    # 图片生成固定计费：额度 = 成功张数 × 每张固定 token（与 public_usage.tool_usage 同源）。
    image_tokens = _image_fixed_tokens(str(payload.get("model") or ""), image_count) if isinstance(payload, dict) else 0
    await settle_in_new_session(
        user.id,
        source="image",
        settlement_key=get_request_id(),
        amount=image_tokens,
        kind="image",
        model=model,
        conversation_id=conversation_id,
        prompt_tokens=0,
        completion_tokens=0,
        image_count=image_count or None,
        extra=extra,
        trace_id=get_request_id(),
    )
    if not isinstance(payload, dict):
        return {}
    payload = dict(payload)
    payload["usage"] = public_usage
    return payload


# 文生视频只开放画布节点用到的这一档，避免把方舟视频接口变成任意模型代理。
VIDEO_MODEL_ID = "doubao-seedance-2-5-flash-261115"
VIDEO_RATIOS = {"adaptive", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"}
VIDEO_RESOLUTIONS = {"480p", "720p"}
VIDEO_DURATION_RANGE = (4, 15)
# 图生视频（带参考图）在画布上最长 6 秒；方舟 Flash 最短 4 秒。
VIDEO_I2V_DURATION_RANGE = (4, 6)
_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def _video_task_id(task_id: str) -> str:
    text = (task_id or "").strip()
    if not _TASK_ID.fullmatch(text):
        raise InvalidRequestError("视频任务 ID 无效")
    return text


def _video_url(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if isinstance(content, dict):
        url = content.get("video_url")
        if isinstance(url, str) and url.strip():
            return url.strip()
        if isinstance(url, dict) and isinstance(url.get("url"), str):
            return url["url"].strip()
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            url = item.get("video_url")
            if isinstance(url, str) and url.strip():
                return url.strip()
            if isinstance(url, dict) and isinstance(url.get("url"), str):
                return url["url"].strip()
    return ""


def _video_tokens(payload: dict[str, Any]) -> int:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    total = int(usage.get("total_tokens") or 0)
    if total <= 0:
        total = int(usage.get("completion_tokens") or 0)
    return max(0, total)


def _ark_error_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str) and error.strip():
            return error.strip()
    return fallback


def _build_video_body(body: dict[str, Any]) -> dict[str, Any]:
    """把画布参数收成 Seedance 视频请求。

    文生视频不传 resolution，交给模型默认。
    图生视频带参考图时提交 resolution（480p / 720p），时长限制在 4–6 秒。
    自动尺寸走首帧（ratio=adaptive）；指定尺寸走参考图，方舟才会采纳 ratio。
    首尾帧同时传 image 与 last_image，比例固定 adaptive，角色为首帧和尾帧。
    """
    model = str(body.get("model") or "").strip()
    if model != VIDEO_MODEL_ID:
        raise InvalidRequestError("当前只支持 Doubao-Seedance-2.5-flash")
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise InvalidRequestError("缺少视频提示词")

    ratio = str(body.get("ratio") or "adaptive").strip() or "adaptive"
    if ratio not in VIDEO_RATIOS:
        raise InvalidRequestError("画幅不在方舟支持的范围内")

    image = body.get("image")
    last_image = body.get("last_image")
    has_image = isinstance(image, str) and bool(image.strip()) and not image.strip().startswith("blob:")
    has_last = isinstance(last_image, str) and bool(last_image.strip()) and not last_image.strip().startswith("blob:")
    low, high = VIDEO_I2V_DURATION_RANGE if has_image or has_last else VIDEO_DURATION_RANGE
    try:
        duration = int(body.get("duration"))
    except (TypeError, ValueError):
        duration = 5
    duration = max(low, min(high, duration))

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt.strip()}]
    payload: dict[str, Any] = {
        "model": model,
        "content": content,
        "ratio": ratio,
        "duration": duration,
        "watermark": False,
    }
    if has_last:
        # Seedance 2.5 首尾帧只接受 adaptive，具体画幅跟随首帧。
        payload["ratio"] = "adaptive"
        if has_image:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image.strip()},
                    "role": "first_frame",
                }
            )
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": last_image.strip()},
                "role": "last_frame",
            }
        )
    elif has_image:
        image_url = image.strip()
        if ratio == "adaptive":
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_url},
                    "role": "first_frame",
                }
            )
        else:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_url},
                    "role": "reference_image",
                }
            )
            payload["omni_reference_task_type"] = "reference"
    resolution = str(body.get("resolution") or "").strip().lower()
    if resolution:
        if resolution not in VIDEO_RESOLUTIONS:
            raise InvalidRequestError("分辨率只支持 480p 或 720p")
        payload["resolution"] = resolution
    seed = body.get("seed")
    if seed is not None and seed != "":
        try:
            payload["seed"] = max(0, min(int(seed), 2**32 - 1))
        except (TypeError, ValueError):
            pass
    return payload


async def _ark_json(settings, method: str, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{settings.ark_base_url.rstrip('/')}/{path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=settings.ark_timeout_seconds) as client:
            response = await client.request(
                method,
                url,
                headers=_auth_headers(settings, json_body=json_body is not None),
                json=json_body,
            )
    except httpx.HTTPError as exc:
        raise UpstreamError("方舟视频生成失败") from exc
    try:
        payload = response.json()
    except Exception as exc:
        raise UpstreamError("方舟视频响应无法解析") from exc
    if response.status_code >= 400:
        raise UpstreamError(_ark_error_message(payload, "方舟视频生成失败"))
    if not isinstance(payload, dict):
        raise UpstreamError("方舟视频响应无法解析")
    return payload


@router.post("/videos/generations", summary="创建 Seedance 文生视频任务")
async def create_video(
    request: Request,
    session: DbSession,
    user: Annotated[User, Depends(get_authenticated_user)],
) -> dict[str, Any]:
    settings = _settings(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise InvalidRequestError("视频请求不是合法 JSON") from exc
    if not isinstance(body, dict):
        raise InvalidRequestError("视频请求格式无效")
    payload = _build_video_body(body)
    await QuotaService(session).authorize(user.id)
    await session.commit()
    created = await _ark_json(settings, "POST", "contents/generations/tasks", json_body=payload)
    task_id = str(created.get("id") or "").strip()
    if not task_id:
        raise UpstreamError("方舟没有返回视频任务 ID")
    return created


@router.get("/videos/generations/{task_id}", summary="查询 Seedance 文生视频任务")
async def get_video(
    task_id: str,
    request: Request,
    user: Annotated[User, Depends(get_authenticated_user)],
) -> dict[str, Any]:
    settings = _settings(request)
    safe_id = _video_task_id(task_id)
    payload = await _ark_json(settings, "GET", f"contents/generations/tasks/{safe_id}")
    url = _video_url(payload)
    if url:
        payload["video_url"] = url
    status = str(payload.get("status") or "")
    if status == "succeeded":
        tokens = _video_tokens(payload)
        await settle_in_new_session(
            user.id,
            source="video",
            settlement_key=f"ark-video:{safe_id}",
            amount=tokens,
            kind="video",
            model=str(payload.get("model") or VIDEO_MODEL_ID),
            completion_tokens=tokens,
            trace_id=get_request_id(),
        )
    return payload


@router.delete("/videos/generations/{task_id}", summary="取消 Seedance 文生视频任务")
async def cancel_video(
    task_id: str,
    request: Request,
    _user: Annotated[User, Depends(get_authenticated_user)],
) -> dict[str, Any]:
    settings = _settings(request)
    safe_id = _video_task_id(task_id)
    try:
        return await _ark_json(settings, "DELETE", f"contents/generations/tasks/{safe_id}")
    except UpstreamError:
        # 已经开始执行或任务不存在时，前端轮询会拿到终态，取消失败不打断本地停止
        return {"id": safe_id, "status": "canceled"}


@router.post("/chat/stream", summary="经服务端转发方舟流式对话并结算额度")
async def stream_chat(
    request: Request,
    session: DbSession,
    user: Annotated[User, Depends(get_authenticated_user)],
) -> StreamingResponse:
    settings = _settings(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise InvalidRequestError("对话请求不是合法 JSON") from exc
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        raise InvalidRequestError("对话请求格式无效")
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise InvalidRequestError("缺少模型")
    await QuotaService(session).authorize(user.id)
    await session.commit()
    url = f"{settings.ark_base_url.rstrip('/')}/chat/completions"
    request_id = get_request_id()

    async def proxy():
        total = prompt_tokens = completion_tokens = 0
        answer_parts: list[str] = []
        meters: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=settings.ark_timeout_seconds) as client:
                async with client.stream(
                    "POST", url, headers=_auth_headers(settings, json_body=True), json=body
                ) as response:
                    if response.status_code >= 400:
                        detail = await response.aread()
                        yield detail
                        return
                    async for line in response.aiter_lines():
                        outgoing = line
                        if line.startswith("data:"):
                            raw = line[5:].strip()
                            if raw and raw != "[DONE]":
                                try:
                                    parsed = json.loads(raw)
                                except json.JSONDecodeError:
                                    parsed = None
                                if isinstance(parsed, dict):
                                    choices = parsed.get("choices")
                                    if isinstance(choices, list) and choices:
                                        delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
                                        if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                                            answer_parts.append(delta["content"])
                                    usage = parsed.get("usage")
                                    if isinstance(usage, dict):
                                        total, prompt_tokens, completion_tokens = _usage_amount(parsed)
                                        try:
                                            from app.metering.attribute import annotate_provider_usage

                                            public, meter = annotate_provider_usage(
                                                provider_name="ark",
                                                model=model,
                                                messages=body.get("messages") or [],
                                                usage_raw=usage,
                                                output_text="".join(answer_parts),
                                                usage_source="ark_chat",
                                            )
                                            parsed["usage"] = public
                                            meters.append(meter)
                                            outgoing = "data: " + json.dumps(parsed, ensure_ascii=False)
                                        except Exception:
                                            pass
                        yield f"{outgoing}\n"
        finally:
            await settle_in_new_session(
                user.id,
                source="ark_chat",
                settlement_key=request_id,
                amount=total,
                kind="chat",
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                meters=meters or None,
                trace_id=request_id,
            )

    return StreamingResponse(proxy(), media_type="text/event-stream")


@router.post("/files", summary="经服务端上传方舟文件")
async def upload_file(
    request: Request,
    session: DbSession,
    user: Annotated[User, Depends(get_authenticated_user)],
    file: UploadFile = File(...),
    purpose: str = Form(default="user_data"),
) -> dict[str, Any]:
    settings = _settings(request)
    if purpose not in {"user_data", "agent"}:
        raise InvalidRequestError("不支持的文件用途")
    content = await file.read()
    if len(content) > _MAX_FILE_BYTES:
        raise InvalidRequestError("文件超过 32MB")
    url = f"{settings.ark_base_url.rstrip('/')}/files"
    try:
        async with httpx.AsyncClient(timeout=settings.ark_timeout_seconds) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {settings.ark_api_key.strip()}"},
                data={"purpose": purpose},
                files={"file": (file.filename or "upload.bin", content)},
            )
    except httpx.HTTPError as exc:
        raise UpstreamError("方舟文件上传失败") from exc
    if response.status_code >= 400:
        raise UpstreamError("方舟文件上传失败")
    try:
        payload = response.json()
    except Exception as exc:
        raise UpstreamError("方舟文件上传失败") from exc
    file_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(file_id, str) or re.fullmatch(_FILE_ID, file_id) is None:
        raise UpstreamError("方舟文件上传失败")
    session.add(ArkFile(user_id=user.id, file_id=file_id, purpose=purpose))
    await session.flush()
    return payload


@router.get("/files/{file_id}", summary="经服务端读取方舟文件状态")
async def retrieve_file(
    file_id: str,
    request: Request,
    session: DbSession,
    user: Annotated[User, Depends(get_authenticated_user)],
) -> dict[str, Any]:
    settings = _settings(request)
    if re.fullmatch(_FILE_ID, file_id) is None:
        raise InvalidRequestError("文件 ID 无效")
    row = await session.scalar(select(ArkFile).where(ArkFile.file_id == file_id))
    if row is None or not owns_ark_file(row.user_id, user.id):
        raise ArkFileNotFoundError()
    url = f"{settings.ark_base_url.rstrip('/')}/files/{file_id}"
    try:
        async with httpx.AsyncClient(timeout=settings.ark_timeout_seconds) as client:
            response = await client.get(
                url, headers={"Authorization": f"Bearer {settings.ark_api_key.strip()}"}
            )
    except httpx.HTTPError as exc:
        raise UpstreamError("方舟文件查询失败") from exc
    if response.status_code >= 400:
        raise UpstreamError("方舟文件查询失败")
    return response.json()
