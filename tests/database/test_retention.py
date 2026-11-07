"""备份保留 30 天。只删除过期的 dump 文件。"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "docker" / "scripts" / "retention.ps1"


def test_retention_removes_dumps_older_than_30_days(tmp_path: Path) -> None:
    old = tmp_path / "muzi_llm-20000101-000000.dump"
    recent = tmp_path / "muzi_llm-20990101-000000.dump"
    note = tmp_path / "notes.txt"
    old.write_bytes(b"old")
    recent.write_bytes(b"new")
    note.write_bytes(b"keep")
    expired = time.time() - 40 * 24 * 3600
    os.utime(old, (expired, expired))

    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-BackupDir",
            str(tmp_path),
            "-KeepDays",
            "30",
        ],
        check=True,
    )

    assert not old.exists()
    assert recent.exists()
    assert note.exists()
