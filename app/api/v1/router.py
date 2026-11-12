"""API v1 路由。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import admin, agents, ark, auth, chat, files, history, mcp, me, models, planning, skills

router = APIRouter()
router.include_router(auth.router)
router.include_router(me.router)
router.include_router(mcp.router)
router.include_router(chat.router)
router.include_router(ark.router)
router.include_router(models.router)
router.include_router(agents.router)
router.include_router(planning.router)
router.include_router(files.router)
router.include_router(skills.router)
router.include_router(history.router)
router.include_router(admin.router)
