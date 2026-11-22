"""API v1 路由。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin,
    agents,
    ark,
    assets,
    auth,
    chat,
    comfy,
    content,
    deepseek,
    docs,
    files,
    flow,
    history,
    internal,
    llm,
    mcp,
    me,
    models,
    planning,
    plugins,
    prompts,
    public,
    recharge,
    share,
    skills,
)

router = APIRouter()
router.include_router(auth.router)
router.include_router(public.router)
router.include_router(internal.router)
router.include_router(recharge.router)
router.include_router(me.router)
router.include_router(mcp.router)
router.include_router(chat.router)
router.include_router(llm.router)
router.include_router(ark.router)
router.include_router(deepseek.router)
router.include_router(models.router)
router.include_router(agents.router)
router.include_router(planning.router)
router.include_router(files.router)
router.include_router(skills.router)
router.include_router(history.router)
router.include_router(flow.router)
router.include_router(flow.store_router)
router.include_router(admin.router)
router.include_router(plugins.router)
router.include_router(prompts.router)
router.include_router(share.router)
router.include_router(docs.router)
router.include_router(content.router)
router.include_router(comfy.router)
router.include_router(assets.router)
