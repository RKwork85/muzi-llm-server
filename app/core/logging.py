"""日志配置。输出会去掉 API Key 和 Authorization 头。"""

from __future__ import annotations

import logging
import re

from app.core.config import Settings

_BEARER = re.compile(r"(?i)(authorization:\s*bearer\s+)(?!%(?:\([^)]+\))?[sdfr])\S+")
_TOKEN = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}")

# 后端工程名。所有模块的 logger 都用这一个名字，改工程名时只改这里。
LOGGER_NAME = "muzi_llm"


def get_logger(name: str = LOGGER_NAME) -> logging.Logger:
    return logging.getLogger(name)


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self._secrets = [item for item in secrets if len(item) >= 8]

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg)
        args = record.args
        if isinstance(args, dict):
            record.args = {
                key: self._redact(value) if isinstance(value, str) else value
                for key, value in args.items()
            }
        elif isinstance(args, tuple):
            record.args = tuple(
                self._redact(value) if isinstance(value, str) else value for value in args
            )
        return True

    def _redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        text = _BEARER.sub(r"\1***", text)
        return _TOKEN.sub(r"\1***", text)


def setup_logging(settings: Settings) -> None:
    logger = get_logger()
    logger.setLevel(settings.log_level)
    logger.propagate = True
    logger.filters.clear()
    logger.addFilter(
        RedactingFilter(
            [
                settings.deepseek_api_key,
                settings.postgres_password,
                settings.redis_password,
            ]
        )
    )
    if not any(getattr(handler, "_muzi_llm", False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        handler._muzi_llm = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    for name in ("httpx", "httpcore", "openai", "httpx2"):
        logging.getLogger(name).setLevel(logging.WARNING)
