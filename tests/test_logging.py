"""日志名统一为后端工程名。"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from app.core.logging import LOGGER_NAME, get_logger, setup_logging
from tests.conftest import make_settings

ROOT = Path(__file__).resolve().parents[1]
_LOGGER_CALL = re.compile(r'getLogger\(\s*"([^"]+)"\s*\)')


def test_logger_name_is_project_name() -> None:
    assert LOGGER_NAME == "muzi_llm"
    assert get_logger().name == LOGGER_NAME


def test_setup_logging_configures_project_logger(caplog) -> None:
    setup_logging(make_settings())
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    get_logger().info("配置生效")
    assert "配置生效" in caplog.text


def test_app_modules_share_one_logger_name() -> None:
    names: dict[str, str] = {}
    for path in (ROOT / "app").rglob("*.py"):
        for name in _LOGGER_CALL.findall(path.read_text(encoding="utf-8")):
            names.setdefault(name, str(path))
    assert names, "app/ 下应至少有一个命名 logger"
    assert set(names) == {LOGGER_NAME}, f"logger 名不一致: {names}"
