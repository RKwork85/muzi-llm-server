"""ComfyUI 图像/视频生成代理接口。

前端带登录态调用本接口；主服务从登录态取真实 user_id，转发给独立 ComfyUI 网关服务
（``muzi-comfy-service``）。网关只信任内部密钥（``X-Internal-Token``），且只在收到该
密钥时才接受 user_id，因此用户无法伪造身份或代他人提交任务（与 ``muzi-pay-service``
同模式）。

网关内部负责：Redis 有序队列排队、模型组亲和调度、串行提交 ComfyUI、轮询结果。
本接口只做收单/查询/取消/工作流列表的透明转发，不含任何排队与调度逻辑。
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.deps import get_authenticated_user
from app.authorization.application.task_quota_service import task_type_of
from app.core.config import get_settings
from app.core.database.deps import DbSession
from app.core.exceptions import (
    ComfyJobConflictError,
    ComfyJobNotFoundError,
    FeatureNotReadyError,
    InvalidRequestError,
    UpstreamError,
    UpstreamRateLimitError,
)
from app.models.assets import GeneratedAsset
from app.models.tables import User

router = APIRouter(prefix="/comfy", tags=["comfy"])

logger = logging.getLogger(__name__)


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
    # 提交时的参数（含 prompt 等），用于落库生成资产。
    params: dict[str, Any] | None = None
    # 时间戳为 Unix 秒，elapsed_ms 为网关实际执行耗时（不含排队）。
    # 前端画布节点用它展示「执行时间」，这里只做透明透传。
    created_at: float | None = None
    started_at: float | None = None
    finished_at: float | None = None
    elapsed_ms: int | None = None
    # 任务类型与本次任务占用的「全局排队」信息（不分用户），由网关下发。
    task_type: str | None = None
    quota_limit: int | None = None
    quota_used: int | None = None
    # 本次任务消耗的额度（token），当前固定 2000，由网关下发、前端展示。
    quota: int | None = None


class ComfyTaskQuotaResponse(BaseModel):
    """查询某工作流所属任务类型的全局排队占用与上限。

    ``queue_limit``：该任务类型的全局排队上限（0 = 不限；正数 = 全局最多同时排队数）。
    ``queue_used``：当前全局已占用的在途/排队任务数。
    ``remaining``：剩余可提交数（不限时为 ``None``）。
    全局维度不分用户：ComfyUI 单实例串行执行，不存在真正并发，占的是全局排队名额。
    """

    task_type: str
    queue_limit: int = 0
    queue_used: int = 0
    remaining: int | None = None


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


async def _persist_assets(session: Any, user_id: Any, job: ComfyJobStatusResponse) -> None:
    """任务成功时把产物幂等写入生成资产表（按 job_id + filename 去重）。

    前端轮询查询接口是主服务唯一能确定感知「任务成功」的入口；网关不访问
    PostgreSQL，所以落库只能由主服务在查询到 succeeded 时补做。
    """
    result = job.result or {}
    outputs = result.get("outputs") or []
    if not outputs:
        return

    prompt = None
    if isinstance(job.params, dict):
        p = job.params.get("prompt")
        if isinstance(p, str) and p.strip():
            prompt = p.strip()

    rows: list[dict[str, Any]] = []
    for o in outputs:
        if not isinstance(o, dict):
            continue
        filename = o.get("filename")
        if not filename:
            continue
        kind = o.get("kind")
        if kind not in ("image", "video"):
            kind = "image"
        rows.append({
            "user_id": user_id,
            "source": "comfy",
            "kind": kind,
            "workflow_id": job.workflow_id or None,
            "job_id": job.job_id or None,
            "prompt": prompt,
            "filename": filename,
            "subfolder": o.get("subfolder") or "",
            "type": o.get("type") or "output",
            "url": o.get("url"),
        })

    if not rows:
        return
    stmt = pg_insert(GeneratedAsset).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["job_id", "filename"])
    await session.execute(stmt)


@router.post("/jobs", response_model=SubmitComfyJobResponse, summary="提交图像/视频生成任务（登录态）")
async def submit_comfy_job(
    body: SubmitComfyJobRequest,
    user: Annotated[User, Depends(get_authenticated_user)],
) -> SubmitComfyJobResponse:
    _require_enabled()
    # 网关按「任务类型全局排队上限」校验准入，主服务不再按用户角色解析额度。
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
    session: DbSession,
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

    job = ComfyJobStatusResponse(**resp.json())
    # 任务成功时把产物落库到生成资产表，供资产页展示。幂等，轮询重复命中不重复写入。
    # 落库失败（如表尚未建、数据库抖动）只记日志，不阻断查询响应，避免前端轮询被连带打挂。
    if job.status == "succeeded":
        try:
            await _persist_assets(session, user.id, job)
        except Exception:  # noqa: BLE001 —— 资产落库是旁路增强，失败不阻断主流程
            logger.exception("生成资产落库失败 job_id=%s", job.job_id)
    return job


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


@router.get("/task-quota", response_model=ComfyTaskQuotaResponse, summary="查询指定工作流所属任务类型的全局排队占用与上限（登录态）")
async def get_comfy_task_quota(
    workflow_id: str,
    user: Annotated[User, Depends(get_authenticated_user)],
) -> ComfyTaskQuotaResponse:
    """前端提交前预检用：拿到该工作流对应的任务类型、全局排队上限与当前全局占用数。

    ``queue_limit=0`` 表示不限；``remaining`` 已扣减当前全局占用数（不限时为 ``None``）。
    最终能否提交仍以网关原子校验为准。
    """
    task_type = task_type_of(workflow_id)
    queue_limit = 0
    queue_used = 0
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                f"{_base_url()}/jobs/quota-usage",
                params={"user_id": str(user.id), "workflow_id": workflow_id},
                headers=_internal_headers(),
            )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                queue_limit = int(data.get("queue_limit") or 0)
                queue_used = int(data.get("queue_used") or 0)
    except httpx.HTTPError:
        # 网关不可用时额度展示降级：只回上限，不阻塞前端
        queue_used = 0

    remaining = None
    if queue_limit > 0:
        remaining = max(0, queue_limit - queue_used)

    return ComfyTaskQuotaResponse(
        task_type=task_type,
        queue_limit=queue_limit,
        queue_used=queue_used,
        remaining=remaining,
    )


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
