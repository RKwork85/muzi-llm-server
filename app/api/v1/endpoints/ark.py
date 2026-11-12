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
    total, prompt_tokens, completion_tokens = _usage_amount(payload if isinstance(payload, dict) else {})
    image_count = len(payload.get("data") or []) if isinstance(payload, dict) else 0
    await settle_in_new_session(
        user.id,
        source="image",
        settlement_key=get_request_id(),
        amount=total,
        kind="image",
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        image_count=image_count or None,
    )
    return payload if isinstance(payload, dict) else {}


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
