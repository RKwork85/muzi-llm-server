"""ComfyUI 图像/视频生成代理接口。

前端带登录态调用本接口；主服务从登录态取真实 user_id，转发给独立 ComfyUI 网关服务
（``muzi-comfy-service``）。网关只信任内部密钥（``X-Internal-Token``），且只在收到该
密钥时才接受 user_id，因此用户无法伪造身份或代他人提交任务（与 ``muzi-pay-service``
同模式）。

网关内部负责：Redis 有序队列排队、模型组亲和调度、串行提交 ComfyUI、轮询结果。
本接口只做收单/查询/取消/工作流列表的透明转发，不含任何排队与调度逻辑。
"""

from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.api.deps import get_authenticated_user
from app.core.config import get_settings
from app.core.exceptions import (
    ComfyJobConflictError,
    ComfyJobNotFoundError,
    FeatureNotReadyError,
    InvalidRequestError,
    UpstreamError,
    UpstreamRateLimitError,
)
from app.models.tables import User

router = APIRouter(prefix="/comfy", tags=["comfy"])


class SubmitComfyJobRequest(BaseModel):
    model_config = {"extra": "forbid"}

    workflow_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=5, ge=0, le=9)


class SubmitComfyJobResponse(BaseModel):
    job_id: str
    status: str
    queue_position: int


class ComfyJobStatusResponse(BaseModel):
    job_id: str
    status: str
    workflow_id: str
    group: str
    queue_position: int | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class ComfyWorkflowItem(BaseModel):
    workflow_id: str
    group: str


class ComfyWorkflowListResponse(BaseModel):
    workflows: list[ComfyWorkflowItem]


def _base_url() -> str:
    return get_settings().comfy_service_base_url.rstrip("/")


def _internal_headers() -> dict[str, str]:
    return {"X-Internal-Token": get_settings().internal_api_token}


def _require_enabled() -> None:
    if not get_settings().internal_api_token:
        raise FeatureNotReadyError("图像生成服务未启用")


def _upstream_detail(resp: httpx.Response, fallback: str) -> str:
    """提取网关返回的 ``detail`` 文案；拿不到就用兜底文案。"""
    try:
        data = resp.json()
        if isinstance(data, dict) and data.get("detail"):
            return str(data["detail"])
    except ValueError:
        pass
    return fallback


@router.post("/jobs", response_model=SubmitComfyJobResponse, summary="提交图像/视频生成任务（登录态）")
async def submit_comfy_job(
    body: SubmitComfyJobRequest,
    user: Annotated[User, Depends(get_authenticated_user)],
) -> SubmitComfyJobResponse:
    _require_enabled()
    url = f"{_base_url()}/jobs"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                headers=_internal_headers(),
                json={
                    "user_id": str(user.id),
                    "workflow_id": body.workflow_id,
                    "params": body.params,
                    "priority": body.priority,
                },
            )
    except httpx.HTTPError as exc:
        raise UpstreamError("图像生成服务暂时不可用") from exc

    if resp.status_code == 422:
        raise InvalidRequestError(_upstream_detail(resp, "未知的生成工作流"))
    if resp.status_code == 429:
        raise UpstreamRateLimitError(_upstream_detail(resp, "生成任务排队已满，请稍后再试"))
    if resp.status_code != 200:
        raise UpstreamError(_upstream_detail(resp, "图像生成服务返回异常"))

    return SubmitComfyJobResponse(**resp.json())


@router.get("/jobs/{job_id}", response_model=ComfyJobStatusResponse, summary="查询生成任务状态（登录态）")
async def get_comfy_job(
    job_id: str,
    user: Annotated[User, Depends(get_authenticated_user)],
) -> ComfyJobStatusResponse:
    _require_enabled()
    url = f"{_base_url()}/jobs/{job_id}"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                url,
                params={"user_id": str(user.id)},
                headers=_internal_headers(),
            )
    except httpx.HTTPError as exc:
        raise UpstreamError("图像生成服务暂时不可用") from exc

    if resp.status_code == 404:
        raise ComfyJobNotFoundError()
    if resp.status_code != 200:
        raise UpstreamError(_upstream_detail(resp, "图像生成服务返回异常"))

    return ComfyJobStatusResponse(**resp.json())


@router.delete("/jobs/{job_id}", summary="取消排队中的生成任务（登录态）")
async def cancel_comfy_job(
    job_id: str,
    user: Annotated[User, Depends(get_authenticated_user)],
) -> dict[str, Any]:
    _require_enabled()
    url = f"{_base_url()}/jobs/{job_id}"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.delete(
                url,
                params={"user_id": str(user.id)},
                headers=_internal_headers(),
            )
    except httpx.HTTPError as exc:
        raise UpstreamError("图像生成服务暂时不可用") from exc

    if resp.status_code == 409:
        raise ComfyJobConflictError()
    if resp.status_code != 200:
        raise UpstreamError(_upstream_detail(resp, "图像生成服务返回异常"))

    return resp.json()


@router.get("/workflows", response_model=ComfyWorkflowListResponse, summary="列出可用生成工作流（登录态）")
async def list_comfy_workflows(
    user: Annotated[User, Depends(get_authenticated_user)],
) -> ComfyWorkflowListResponse:
    """前端选择工作流时用它。返回网关加载到的全部 workflow（含模型组）。"""
    _require_enabled()
    url = f"{_base_url()}/workflows"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(url, headers=_internal_headers())
    except httpx.HTTPError as exc:
        raise UpstreamError("图像生成服务暂时不可用") from exc

    if resp.status_code != 200:
        raise UpstreamError(_upstream_detail(resp, "图像生成服务返回异常"))

    return ComfyWorkflowListResponse(**resp.json())


@router.get("/jobs/{job_id}/files/{filename}", summary="读取生成产物文件（登录态）")
async def get_comfy_job_file(
    job_id: str,
    filename: str,
    user: Annotated[User, Depends(get_authenticated_user)],
    subfolder: str = "",
    type: str = "output",
) -> Response:
    """把生成产物（图片/视频）原样回传给前端。

    网关未配置 OSS 时结果里只有 ComfyUI 本地文件名，前端靠这个接口取二进制，
    拿到后自行转 object URL 展示（因此可以带 Authorization 头，不暴露 token）。
    """
    _require_enabled()
    url = f"{_base_url()}/jobs/{job_id}/files/{filename}"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                url,
                params={"user_id": str(user.id), "subfolder": subfolder, "type": type},
                headers=_internal_headers(),
            )
    except httpx.HTTPError as exc:
        raise UpstreamError("读取生成产物失败，请稍后重试") from exc

    if resp.status_code == 404:
        raise ComfyJobNotFoundError()
    if resp.status_code == 409:
        raise ComfyJobConflictError("任务尚未成功完成")
    if resp.status_code != 200:
        raise UpstreamError(_upstream_detail(resp, "读取生成产物失败"))

    return Response(
        content=resp.content,
        media_type=resp.headers.get("content-type", "application/octet-stream"),
        headers={"Cache-Control": "private, max-age=86400"},
    )
