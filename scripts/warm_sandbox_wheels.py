"""把白名单依赖的轮子预下载到 sandbox_wheel_cache。

运行期装依赖走 ``pip --no-index --find-links <cache>``，只解压本地轮子，不联网、不编译。
实测镜像对未缓存 wheel 的下发很不稳定（2MB 的 reportlab 也可能 >60 秒），所以必须在
部署时把轮子灌好，而不是等用户请求时现下。

用法：
    python scripts/warm_sandbox_wheels.py            # 预热白名单全部
    python scripts/warm_sandbox_wheels.py pillow pypdf   # 只预热指定包

幂等：已存在的轮子会跳过。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import ROOT, get_settings  # noqa: E402


def main(argv: list[str]) -> int:
    settings = get_settings()
    raw = str(settings.sandbox_dependency_allowlist or "")
    names = [part.strip() for part in raw.split(",") if part.strip()]
    requested = [item for item in argv if not item.startswith("-")]
    packages = requested or names
    if not packages:
        print("白名单为空，无需预热。")
        return 1

    cache = Path(settings.sandbox_dependency_cache_dir or "sandbox_wheel_cache")
    if not cache.is_absolute():
        cache = ROOT / cache
    cache.mkdir(parents=True, exist_ok=True)

    argv_pip = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--no-input",
        "--disable-pip-version-check",
        "--only-binary=:all:",
        "--dest",
        str(cache),
        *packages,
    ]
    print(f"目标目录：{cache}")
    print(f"预热 {len(packages)} 个顶层包（含传递依赖）……")
    result = subprocess.run(argv_pip, shell=False)
    if result.returncode != 0:
        print("预热失败：检查网络或镜像配置后重试（可只预热部分包）。")
        return result.returncode

    wheels = sorted(cache.glob("*.whl"))
    total_mb = sum(item.stat().st_size for item in wheels) / (1024 * 1024)
    print(f"完成：{len(wheels)} 个轮子，共 {total_mb:.1f}MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
