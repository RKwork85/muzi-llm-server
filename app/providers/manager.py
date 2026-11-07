"""Provider 生命周期管理。"""

from __future__ import annotations

from app.core.config import Settings
from app.core.exceptions import ConfigurationError
from app.providers.base import LLMProvider
from app.providers.deepseek import DeepSeekProvider


class ProviderManager:
    def __init__(self, settings: Settings, provider: LLMProvider | None = None) -> None:
        self._settings = settings
        self._provider = provider

    async def startup(self) -> None:
        if self._provider is None:
            self._provider = DeepSeekProvider(self._settings)
        startup = getattr(self._provider, "startup", None)
        if startup is not None:
            await startup()

    async def shutdown(self) -> None:
        if self._provider is not None:
            await self._provider.aclose()

    @property
    def provider(self) -> LLMProvider:
        if self._provider is None:
            raise ConfigurationError()
        return self._provider
