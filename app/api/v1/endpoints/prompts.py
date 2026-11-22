"""内置提示词管理：列出服务端全部系统提示词，并支持启用 / 关闭。

读取只要求登录（提示词内容对排障有用）；写入会改变所有人的对话行为，
因此要求 ``admin.access``。开关状态落盘在 ``data_store/prompt_settings.json``，
下一次组装提示词立即生效，不需要重启。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_authenticated_user
from app.authorization.application.authorization_service import AuthorizationService
from app.authorization.dependencies import require_permission
from app.core.database.deps import DbSession
from app.core.exceptions import InvalidRequestError
from app.models.tables import User
from app.prompts.catalog import build_catalog
from app.prompts.settings import disabled_prompts, replace_disabled, set_enabled

router = APIRouter(prefix="/prompts", tags=["prompts"])
AuthenticatedUser = Annotated[User, Depends(get_authenticated_user)]


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
async def list_prompts(
    user: AuthenticatedUser,
    session: DbSession,
) -> PromptCatalogOut:
    can_manage = await AuthorizationService(session).has_permission(user.id, "admin.access")
    return _catalog(can_manage)


@router.patch("/{prompt_id}", response_model=PromptCatalogOut, summary="启用或关闭单条内置提示词")
async def toggle_prompt(
    prompt_id: str,
    body: PromptToggleIn,
    actor: Annotated[User, Depends(require_permission("admin.access"))],
) -> PromptCatalogOut:
    del actor
    if prompt_id not in _known_ids():
        raise InvalidRequestError("提示词不存在或已下线")
    set_enabled(prompt_id, body.enabled)
    return _catalog(True)


@router.put("/state", response_model=PromptCatalogOut, summary="批量设置关闭的内置提示词")
async def replace_prompt_state(
    body: PromptStateIn,
    actor: Annotated[User, Depends(require_permission("admin.access"))],
) -> PromptCatalogOut:
    del actor
    known = _known_ids()
    unknown = sorted({item for item in body.disabled if item not in known})
    if unknown:
        raise InvalidRequestError(f"提示词不存在或已下线: {', '.join(unknown[:5])}")
    replace_disabled(body.disabled)
    return _catalog(True)
