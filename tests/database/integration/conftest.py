"""需要本机 Docker 数据库。默认测试套件不进入这里。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

if os.environ.get("MUZI_DB_TESTS") != "1":
    pytest.skip("设置 MUZI_DB_TESTS=1 后运行数据库集成测试", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session", autouse=True)
def upgraded_database() -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        check=True,
    )
