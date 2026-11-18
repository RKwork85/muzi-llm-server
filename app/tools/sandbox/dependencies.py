"""沙盒按需装依赖的限制与判定。

核心结论来自实测：**请求时联网装包不可靠**。同一个镜像，``pypdf`` 冷装 2.9 秒，而
``reportlab``（2MB）、``numpy``（15MB）、``imageio`` 全部超过 60 秒。所以不能靠「调大
超时」解决问题，而是改成两段式：

1. **部署期预热**：``scripts/warm_sandbox_wheels.py`` 把白名单里的轮子全部下载进
   ``sandbox_wheel_cache``（只做一次，运维控制时机与带宽）。
2. **运行期离线安装**：``pip --no-index --find-links <cache>``，只做解压，秒级完成，
   不依赖网络，也不受镜像抖动影响。缓存里没有的包直接失败并提示去预热。

判定顺序（任一不满足就拒绝，并把原因返回给模型）：

1. 开关关闭 → ``DEPENDENCIES_DISABLED``
2. 包名（去掉版本约束后）命中硬禁令 → ``DEPENDENCY_FORBIDDEN``
3. 个数超过 ``sandbox_dependency_max_packages`` → ``DEPENDENCY_TOO_MANY``
4. 包名不在 ``sandbox_dependency_allowlist`` → ``DEPENDENCY_NOT_ALLOWED``
5. 缓存里找不到对应轮子 → ``DEPENDENCY_NOT_WARMED``（提示运行预热脚本）
6. 只用 ``--only-binary=:all:``：绝不在请求路径上触发本地编译
7. 装完总量超过 ``sandbox_dependency_max_mb`` → ``DEPENDENCY_TOO_LARGE``

三个阈值的口径（都按「一次安装」算，不是按每个包）：

- ``max_packages``：**顶层包名个数**。传递依赖不计数（``python-docx`` 会带上
  ``lxml``、``typing-extensions``，但它们不算进 6 个里）。
- ``max_mb``：**装完之后 .deps 目录的总字节数**，含传递依赖。真正卡体积的是它。
- ``timeout_seconds``：**整次 pip 调用的总预算**，不是每个包各算一次。因为走本地
  缓存，这个预算只需要覆盖解压。

技能自带的依赖（SKILL.md 里声明的）走 ``trusted=True``：跳过白名单但仍受硬禁令、
体积与时长限制。技能清单是审核过的，不能因为它多装一个包就整个技能不可用；但模型
在 ``run_python`` 里自己点名要装的包必须落在白名单内。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SPEC_SPLIT = re.compile(r"[<>=!~\[\s]")
DEPS_DIR = ".deps"

# 无论白名单怎么写都拒绝：编译型大包、ML 框架、浏览器自动化（要下载浏览器）。
_HARD_DENY = frozenset(
    {
        "torch",
        "pytorch",
        "tensorflow",
        "tensorflow-cpu",
        "tensorflow-gpu",
        "jax",
        "jaxlib",
        "transformers",
        "accelerate",
        "scikit-learn",
        "sklearn",
        "scipy",
        "numba",
        "playwright",
        "pyppeteer",
        "selenium",
        "onnxruntime",
        "onnxruntime-gpu",
    }
)


class DependencyRejected(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class DependencyPlan:
    packages: tuple[str, ...]
    timeout: float
    max_bytes: int
    cache_dir: Path | None
    offline_from_cache: bool


def allowlist(settings) -> frozenset[str]:
    raw = str(getattr(settings, "sandbox_dependency_allowlist", "") or "")
    names = {_normalize(part) for part in raw.split(",")}
    return frozenset(name for name in names if name)


def plan(spec_dependencies: tuple[str, ...], settings, *, trusted: bool = False) -> DependencyPlan:
    """校验并规划一次安装；不合法时抛 ``DependencyRejected``。

    ``trusted=True`` 用于技能清单声明的依赖：跳过白名单（这些包由技能作者声明、
    经审核），但硬禁令、数量、体积、时长一律照旧。
    """
    packages = tuple(dict.fromkeys(_clean(item) for item in spec_dependencies if _clean(item)))
    if not packages:
        raise DependencyRejected("DEPENDENCY_INVALID", "依赖列表为空")
    if not getattr(settings, "sandbox_dependencies_enabled", True):
        raise DependencyRejected(
            "DEPENDENCIES_DISABLED",
            "当前关闭了沙盒安装依赖（SANDBOX_DEPENDENCIES_ENABLED=false），只能用标准库与执行环境已有的包。",
        )
    cache = _cache_dir(settings)
    offline_from_cache = bool(
        cache is not None and getattr(settings, "sandbox_dependency_from_cache", True)
    )
    if not offline_from_cache and not getattr(settings, "sandbox_network_enabled", True):
        raise DependencyRejected("DEPENDENCY_OFFLINE", "沙盒已断网，无法安装依赖")
    limit = int(getattr(settings, "sandbox_dependency_max_packages", 6) or 6)
    if len(packages) > limit:
        raise DependencyRejected(
            "DEPENDENCY_TOO_MANY",
            f"一次最多安装 {limit} 个顶层包（传递依赖不计入），本次请求了 {len(packages)} 个。",
        )
    permitted = allowlist(settings)
    for package in packages:
        base = _normalize(_SPEC_SPLIT.split(package, maxsplit=1)[0])
        if base in _HARD_DENY:
            raise DependencyRejected(
                "DEPENDENCY_FORBIDDEN",
                f"{base} 属于明确禁止的编译型/大型包，请改用标准库或其他工具实现。",
            )
        if trusted:
            continue
        if base not in permitted:
            allowed = "、".join(sorted(permitted)) or "（无）"
            raise DependencyRejected(
                "DEPENDENCY_NOT_ALLOWED",
                f"不允许安装 {base}；当前可用：{allowed}。"
                "需要其他能力时改用已有工具或 stdlib 实现。",
            )
    return DependencyPlan(
        packages=packages,
        timeout=float(getattr(settings, "sandbox_dependency_timeout_seconds", 45) or 45),
        max_bytes=int(getattr(settings, "sandbox_dependency_max_mb", 32) or 32) * 1024 * 1024,
        cache_dir=cache,
        offline_from_cache=offline_from_cache,
    )


def pip_argv(python: str, plan_: DependencyPlan, target: Path) -> list[str]:
    """安装命令：只取轮子、不升级核心依赖、装进沙盒自己的目录。

    走缓存时用 ``--no-index --find-links``：完全不联网，只解压本地轮子。
    """
    argv = [
        python,
        "-m",
        "pip",
        "install",
        "--no-input",
        "--disable-pip-version-check",
        "--no-warn-script-location",
        "--only-binary=:all:",
        "--target",
        str(target),
    ]
    if plan_.offline_from_cache and plan_.cache_dir is not None:
        argv.extend(["--no-index", "--find-links", str(plan_.cache_dir)])
    argv.extend(plan_.packages)
    return argv


def installed_bytes(target: Path) -> int:
    total = 0
    for item in target.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _clean(item: str) -> str:
    text = str(item or "").strip()
    if not text or len(text) > 80:
        return ""
    base = _SPEC_SPLIT.split(text, maxsplit=1)[0]
    return text if _NAME.fullmatch(base) else ""


def _normalize(name: str) -> str:
    return str(name or "").strip().lower().replace("_", "-")


def _cache_dir(settings) -> Path | None:
    raw = str(getattr(settings, "sandbox_dependency_cache_dir", "") or "").strip()
    if not raw:
        return None
    try:
        from app.core.config import ROOT

        path = Path(raw)
        if not path.is_absolute():
            path = ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return None
