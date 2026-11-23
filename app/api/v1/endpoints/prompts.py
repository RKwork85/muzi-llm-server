"""内置提示词管理：列出服务端全部系统提示词，并支持启用 / 关闭。

这些是平台级配置：关掉一条人设或安全约束会改变**所有用户**的每一轮请求，
所以读写都要求 ``prompt.manage``（80 级管理权限）。

不要用 ``admin.access`` 当门禁：它是「进入管理后台页面」的浏览码，等级 20，
可以勾给访客或任意自定义角色。把它当写权限用，等于「能进后台」就「能关掉
所有人的系统提示词」，违背页面准入与页面功能两条边界的约定。

开关状态落盘在 ``data_store/prompt_settings.json``，下一次组装提示词立即生效，
不需要重启。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.authorization.dependencies import require_permission
from app.core.exceptions import InvalidRequestError
from app.models.tables import User
from app.prompts.catalog import build_catalog
from app.prompts.settings import disabled_prompts, replace_disabled, set_enabled

router = APIRouter(prefix="/prompts", tags=["prompts"])
PromptManager = Annotated[User, Depends(require_permission("prompt.manage"))]


class PromptItemOut(BaseModel):
    id: str
    category: str
    name: str
    summary: str
    source: str
    content: str
    effect: str
    enabled: bool


class PromptGroupOut(BaseModel):
    id: str
    name: str
    description: str
    items: list[PromptItemOut] = Field(default_factory=list)


class PromptCatalogOut(BaseModel):
    categories: list[PromptGroupOut] = Field(default_factory=list)
    disabled: list[str] = Field(default_factory=list)
    can_manage: bool = False
    total: int = 0
    enabled_count: int = 0


class PromptToggleIn(BaseModel):
    enabled: bool


class PromptStateIn(BaseModel):
    disabled: list[str] = Field(default_factory=list, max_length=400)


def _catalog(can_manage: bool) -> PromptCatalogOut:
    groups = build_catalog()
    total = sum(len(group.items) for group in groups)
    enabled_count = sum(1 for group in groups for item in group.items if item.enabled)
    return PromptCatalogOut(
        categories=[
            PromptGroupOut(
                id=group.id,
                name=group.name,
                description=group.description,
                items=[
                    PromptItemOut(
                        id=item.id,
                        category=item.category,
                        name=item.name,
                        summary=item.summary,
                        source=item.source,
                        content=item.content,
                        effect=item.effect,
                        enabled=item.enabled,
                    )
                    for item in group.items
                ],
            )
            for group in groups
        ],
        disabled=sorted(disabled_prompts()),
        can_manage=can_manage,
        total=total,
        enabled_count=enabled_count,
    )


def _known_ids() -> set[str]:
    return {item.id for group in build_catalog() for item in group.items}


@router.get("", response_model=PromptCatalogOut, summary="列出后端内置提示词与开关状态")
async def list_prompts(actor: PromptManager) -> PromptCatalogOut:
    # 读也只对管理者开放：目录里有全部安全约束与工具 schema 原文，
    # 普通用户没有使用场景，读出去反而方便构造提示词注入。
    del actor
    return _catalog(True)


@router.patch("/{prompt_id}", response_model=PromptCatalogOut, summary="启用或关闭单条内置提示词")
async def toggle_prompt(
    prompt_id: str,
    body: PromptToggleIn,
    actor: PromptManager,
) -> PromptCatalogOut:
    del actor
    if prompt_id not in _known_ids():
        raise InvalidRequestError("提示词不存在或已下线")
    set_enabled(prompt_id, body.enabled)
    return _catalog(True)


@router.put("/state", response_model=PromptCatalogOut, summary="批量设置关闭的内置提示词")
async def replace_prompt_state(
    body: PromptStateIn,
    actor: PromptManager,
) -> PromptCatalogOut:
    del actor
    known = _known_ids()
    unknown = sorted({item for item in body.disabled if item not in known})
    if unknown:
        raise InvalidRequestError(f"提示词不存在或已下线: {', '.join(unknown[:5])}")
    replace_disabled(body.disabled)
    return _catalog(True)
