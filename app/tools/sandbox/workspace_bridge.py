"""把「工作区能不能被沙盒看见」收敛成一次运行期探测。

历史坑：``run_python`` 的描述写死了「工作区只读挂载在 /workspace」，但默认后端
是 ``local``（临时目录 + 本地进程），根本没有挂载点，模型于是花好几轮去猜
「我到底能不能读文件」。描述必须跟着实际后端走，所以这里把探测结果做成唯一
事实来源，工具描述和子进程环境都从它派生。

两种暴露方式：

    - ``container``：agentbox 后端，工作区只读挂在容器 ``/workspace``，产物目录
      读写挂在 ``/output``。
    - ``path``：local 后端，工作区根路径通过 ``MUZI_WORKSPACE`` 注入子进程；子进程
      实际拿到的是本机工作区的**只读快照**（见 subprocess_backend），可写目录只有
      ``MUZI_OUTPUT``。
    - ``none``：工作区未配置、不可读，或探测失败——描述里必须明确说「读不到」。

工作区一律按**用户 scope 目录**解析，和 ``deliver_file`` 用同一个 ``workspace_root()``：
历史实现两边各算一套，沙盒看到的是工作区根、交付却去 scope 子目录找，产物永远交付不到。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from app.tools.sandbox.outputs import OUTPUT_DIRNAME, OUTPUT_ENV, output_dir

WORKSPACE_ENV = "MUZI_WORKSPACE"

_MODE_CONTAINER = "container"
_MODE_PATH = "path"
_MODE_NONE = "none"
_CONTAINER_OUTPUT = "/output"


@dataclass(frozen=True)
class WorkspaceBridge:
    """工作区在沙盒里的可见性。

    ``mode`` 决定描述文案；``path`` 是模型该用的路径（容器内或本机绝对路径）；
    ``output`` 是唯一可写的产物目录（容器内或本机绝对路径，空表示不可用）；
    ``env`` 是要注入子进程的环境变量。
    """

    mode: str
    path: str = ""
    output: str = ""
    readonly: bool = True
    env: dict[str, str] = field(default_factory=dict)

    @property
    def visible(self) -> bool:
        return self.mode != _MODE_NONE

    def describe(self) -> str:
        if self.mode == _MODE_CONTAINER:
            return (
                f"用户工作区以只读方式挂载在 {self.path} 目录，"
                f"用 open('{self.path}/文件名') 读取（例如用 pypdf 读 PDF 提取文本）。"
                f"可写目录只有 {self.output or _CONTAINER_OUTPUT}（环境变量 {OUTPUT_ENV}），"
                f"写在别处的文件执行结束即丢弃。"
                f"需要交付的产物（任何格式：.xmind/.zip/.pdf/图片/音视频）必须写进该目录，"
                f"再用 deliver_file 传 \"{OUTPUT_DIRNAME}/文件名\"。"
            )
        if self.mode == _MODE_PATH:
            return (
                f"工作区通过环境变量 {WORKSPACE_ENV} 提供，里面是本次执行的**只读快照**："
                f"可以读，写它不会影响真实工作区，也不要在那里找上一次的产物。"
                f"唯一可写且会保留的目录是环境变量 {OUTPUT_ENV}（{self.output or '未配置'}）。"
                f"需要交付的产物（任何格式：.xmind/.zip/.pdf/图片/音视频）都必须写进 {OUTPUT_ENV}，"
                f"再用 deliver_file 传 \"{OUTPUT_DIRNAME}/文件名\"；写进沙盒临时目录或快照里的文件不会保留。"
            )
        return (
            "本次执行看不到用户工作区（未配置工作区或当前后端无法暴露），"
            f"不要尝试 open('/workspace/...')；只做纯计算，需要读文件时改用 read_text_file 工具。"
            f"可写产物目录同样不可用，需要生成文件时改用 write_text_file / write_docx。"
        )


def detect_workspace(settings, workspace_root: Path | None = None) -> WorkspaceBridge:
    """按后端与运行期条件探测工作区可见性。

    「检测环境」的意义：本地开发默认 local，部署后可能切 agentbox 或整包进 Docker。
    这里同时检查后端配置与 docker 可用性，避免描述承诺一个用不了的路径。
    """
    backend = getattr(settings, "sandbox_backend", "local")
    if backend == "agentbox" and shutil.which("docker"):
        if getattr(settings, "sandbox_workspace_access", True) is False:
            return WorkspaceBridge(_MODE_NONE)
        return WorkspaceBridge(
            _MODE_CONTAINER,
            path="/workspace",
            output=_CONTAINER_OUTPUT,
            readonly=True,
            env={OUTPUT_ENV: _CONTAINER_OUTPUT},
        )

    if getattr(settings, "sandbox_workspace_access", True) is False:
        return WorkspaceBridge(_MODE_NONE)
    root = workspace_root or _resolve_root(settings)
    if root is None:
        return WorkspaceBridge(_MODE_NONE)
    target = output_dir(settings, create=True)
    # local：Windows 没有 bind mount，只能给绝对路径。仍然只在环境变量里出现，
    # 不放宽文件工具自己的越界校验。
    return WorkspaceBridge(
        _MODE_PATH,
        path=str(root),
        output=str(target) if target is not None else "",
        readonly=True,
        env={WORKSPACE_ENV: str(root), **({OUTPUT_ENV: str(target)} if target is not None else {})},
    )


def sandbox_env(settings, bridge: WorkspaceBridge, offline: bool) -> dict[str, str]:
    """子进程环境：工作区路径 + 产物目录 + 离线开关。"""
    env: dict[str, str] = dict(bridge.env)
    if not bridge.visible:
        env.pop(WORKSPACE_ENV, None)
        env.pop(OUTPUT_ENV, None)
    if offline:
        env.update(_offline_env())
    return env


def _offline_env() -> dict[str, str]:
    """协作式断网：让常见网络客户端快速失败，而不是挂到超时。

    注意这是「尽力而为」：本地进程没有内核级网络隔离，直接开 socket 仍能出去。
    真正需要强隔离时必须用 agentbox 的 --network none。
    """
    blackhole = "http://127.0.0.1:9"
    return {
        "HTTP_PROXY": blackhole,
        "HTTPS_PROXY": blackhole,
        "ALL_PROXY": blackhole,
        "http_proxy": blackhole,
        "https_proxy": blackhole,
        "all_proxy": blackhole,
        "NO_PROXY": "",
        "no_proxy": "",
        "PIP_NO_INDEX": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
        "npm_config_offline": "true",
        "NPM_CONFIG_OFFLINE": "true",
    }


def _resolve_root(settings) -> Path | None:
    """解析工作区 scope 目录；失败返回 None，由调用方降级成「看不到工作区」。

    必须和 ``deliver_file`` 共用 ``workspace_root()``：它会把
    ``CURRENT_WORKSPACE_SCOPE`` 拼进去。两边各算一套是历史 bug。
    """
    try:
        from app.tools.file_sandbox import FileDenied
        from app.tools.file_tools import workspace_root

        return workspace_root(settings)
    except FileDenied:
        return None
    except OSError:
        return None
