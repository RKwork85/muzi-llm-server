"""API v1 路由。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import agents, auth, chat, files, mcp, models, skills

router = APIRouter()
router.include_router(auth.router)
router.include_router(mcp.router)
router.include_router(chat.router)
router.include_router(models.router)
router.include_router(agents.router)
router.include_router(files.router)
router.include_router(skills.router)
