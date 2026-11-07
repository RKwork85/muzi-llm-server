"""运行配置。模型 ID、地址、超时和工具轮数都从这里读取。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py -> core -> app -> 服务根目录
ROOT = Path(__file__).resolve().parents[2]


def safe_validation_text(exc: ValidationError) -> str:
    """只保留字段位置和校验说明，并抹掉输入值里的密钥。"""
    secrets: list[str] = []
    parts: list[str] = []
    for err in exc.errors():
        _collect_secret_strings(err.get("input"), secrets)
        loc = ".".join(str(item) for item in err.get("loc", ()))
        message = str(err.get("msg", "invalid"))
        parts.append(f"{loc}: {message}" if loc else message)
    text = "; ".join(parts) if parts else "配置无效"
    for secret in secrets:
        text = text.replace(secret, "***")
    return text


def _collect_secret_strings(value: Any, found: list[str]) -> None:
    if isinstance(value, str):
        if len(value) >= 8:
            found.append(value)
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_secret_strings(item, found)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_secret_strings(item, found)


class Settings(BaseSettings):
    """默认模型 ID 对齐 DeepSeek 官方文档，核验日期 2026-11-06。

    官方 Flash API 名为 deepseek-flash，base_url 为 https://api.deepseek.com。
    这些只作为可覆盖的配置默认值，业务模块不要再写死。
    """

    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "DeepSeek FastAPI Phase 1"
    llm_provider: str = "deepseek"
    deepseek_api_key: str = Field(default="", repr=False)
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    deepseek_model_allowlist: str = "deepseek-flash,deepseek-v4-pro"
    deepseek_timeout_seconds: float = Field(default=60, gt=0, le=600)
    deepseek_max_retries: int = Field(default=2, ge=0, le=5)
    deepseek_retry_backoff_seconds: float = Field(default=0.2, ge=0, le=5)
    # 上游省略 thinking 时默认开启思考。本服务显式下发，避免温度和工具参数行为不稳定。
    deepseek_thinking: Literal["enabled", "disabled"] = "disabled"
    log_level: str = "INFO"
    require_api_key_on_startup: bool = False
    expose_reasoning: bool = False
    max_tool_rounds: int = 3
    tool_round_hard_limit: int = 5
    tool_timeout_seconds: float = Field(default=2, gt=0, le=30)
    tool_result_max_chars: int = Field(default=4000, ge=96, le=20000)
    agent_max_active_tasks: int = Field(default=8, ge=1, le=32)
    agent_task_ttl_seconds: float = Field(default=300, ge=1, le=3600)
    agent_sync_tool_concurrency: int = Field(default=2, ge=1, le=8)
    agent_cancel_cleanup_seconds: float = Field(default=2, gt=0, le=30)
    tools_network_enabled: bool = False
    tools_workspace_root: str = ""
    tools_network_concurrency: int = Field(default=4, ge=1, le=8)
    agent_max_artifacts: int = Field(default=5, ge=1, le=8)
    agent_max_artifact_chars: int = Field(default=20000, ge=256, le=20000)
    web_tools_max_retries: int = Field(default=0, ge=0, le=1)
    web_tools_retry_backoff_seconds: float = Field(default=0.2, ge=0, le=2)
    web_fetch_url_policy: Literal["provenance", "allowlist", "any"] = "provenance"
    web_search_send_ori_question: bool = False
    web_response_max_bytes: int = Field(default=2_000_000, ge=1024, le=2_000_000)
    web_fetch_allowed_domains: str = ""
    web_fetch_denied_domains: str = ""
    web_fetch_block_credential_query: bool = True
    web_search_max_calls: int = Field(default=5, ge=1, le=8)
    web_fetch_max_calls: int = Field(default=5, ge=1, le=8)
    data_store_root: str = "data_store"
    data_file_ttl_seconds: float = Field(default=3600, ge=60, le=86400)
    data_download_ttl_seconds: float = Field(default=600, ge=30, le=3600)
    data_max_rows: int = Field(default=5000, ge=1, le=20000)
    data_max_bytes: int = Field(default=1_000_000, ge=1024, le=5_000_000)
    data_max_columns: int = Field(default=64, ge=1, le=128)
    data_sample_rows: int = Field(default=3, ge=0, le=5)
    data_max_package_files: int = Field(default=20, ge=1, le=50)
    data_max_package_bytes: int = Field(default=2_000_000, ge=1024, le=8_000_000)
    oss_upload_base_url: str = ""
    oss_upload_prefix: str = "agent-data"
    oss_upload_timeout_seconds: float = Field(default=20, gt=0, le=30)

    @field_validator("deepseek_base_url")
    @classmethod
    def _normalize_base_url(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("DEEPSEEK_BASE_URL 必须以 http:// 或 https:// 开头")
        if cleaned.endswith("/chat/completions"):
            raise ValueError("DEEPSEEK_BASE_URL 不应包含 /chat/completions")
        return cleaned

    @field_validator("oss_upload_base_url")
    @classmethod
    def _normalize_oss_url(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if not cleaned:
            return ""
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.path:
            raise ValueError("OSS_UPLOAD_BASE_URL 必须是不带路径和账号的 http(s) 地址")
        return cleaned

    @field_validator("oss_upload_prefix")
    @classmethod
    def _normalize_oss_prefix(cls, value: str) -> str:
        import re

        cleaned = value.strip().strip("/")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,40}", cleaned) is None:
            raise ValueError("OSS_UPLOAD_PREFIX 不合法")
        return cleaned

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL 无效")
        return normalized

    @field_validator("deepseek_model", "llm_provider")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("配置项不能为空")
        return cleaned

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        if self.require_api_key_on_startup and not self.deepseek_api_key.strip():
            raise ValueError("DEEPSEEK_API_KEY 未配置")
        allowlist = self.model_allowlist()
        if not allowlist:
            raise ValueError("DEEPSEEK_MODEL_ALLOWLIST 不能为空")
        if self.deepseek_model not in allowlist:
            raise ValueError("默认模型不在 DEEPSEEK_MODEL_ALLOWLIST 中")
        if not 1 <= self.tool_round_hard_limit <= 5:
            raise ValueError("工具轮数硬上限必须在 1 到 5 之间")
        if not 1 <= self.max_tool_rounds <= self.tool_round_hard_limit:
            raise ValueError("MAX_TOOL_ROUNDS 超出允许范围")
        return self

    def model_allowlist(self) -> list[str]:
        seen: list[str] = []
        for part in self.deepseek_model_allowlist.split(","):
            item = part.strip()
            if item and item not in seen:
                seen.append(item)
        return seen

    def has_api_key(self) -> bool:
        return bool(self.deepseek_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_settings() -> Settings:
    try:
        return get_settings()
    except ValidationError as exc:
        raise RuntimeError(safe_validation_text(exc)) from None
