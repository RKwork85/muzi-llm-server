"""允许调用的模型列表来自配置白名单，不等于上游实时全量模型。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_settings_dep
from app.core.config import Settings
from app.schemas.common import ModelCard, ModelListResponse

router = APIRouter(tags=["models"])


@router.get("/models", response_model=ModelListResponse, summary="列出允许的模型")
def list_models(settings: Settings = Depends(get_settings_dep)) -> ModelListResponse:
    return ModelListResponse(
        data=[
            ModelCard(id=model_id, provider=settings.llm_provider)
            for model_id in settings.model_allowlist()
        ]
    )
