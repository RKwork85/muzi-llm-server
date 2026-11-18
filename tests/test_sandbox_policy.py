"""沙盒策略单测：工具终态、依赖限制、工作区探测与超时保留输出。"""

from __future__ import annotations

import json

import pytest

from app.services.chat_service import _tool_status
from app.tools.code_runner import PythonArgs, run_python
from app.tools.sandbox import dependencies as dep_policy
from app.tools.sandbox.describe import sandbox_tool_description
from app.tools.sandbox.workspace_bridge import WORKSPACE_ENV, detect_workspace
from app.tools.web_tools import CURRENT_SETTINGS
from tests.conftest import make_settings


# ---------- 工具终态 ----------


def test_tool_status_distinguishes_timeout_from_failure_and_success() -> None:
    ok = json.dumps({"exit_code": 0, "output": "42"})
    assert _tool_status(ok) == ("completed", "")

    timed_out = json.dumps(
        {"error": {"code": "code_timeout", "message": "超时"}, "output": "step 1"}
    )
    assert _tool_status(timed_out) == ("timeout", "code_timeout")

    failed = json.dumps({"error": {"code": "file_unavailable", "message": "路径不可用"}})
    assert _tool_status(failed) == ("failed", "file_unavailable")

    # 技能脚本超时用大写错误码
    assert _tool_status(json.dumps({"error": {"code": "SKILL_EXECUTION_TIMEOUT"}})) == (
        "timeout",
        "SKILL_EXECUTION_TIMEOUT",
    )
    # 网络类工具失败时 result 直接是错误码字符串
    assert _tool_status("WEB_FETCH_FAILED") == ("failed", "WEB_FETCH_FAILED")
    # 非 JSON 的正常结果不硬判为失败
    assert _tool_status("hello") == ("completed", "")


# ---------- 依赖策略 ----------


@pytest.mark.parametrize(
    ("deps", "overrides", "code"),
    [
        (("tweepy",), {}, "DEPENDENCY_NOT_ALLOWED"),
        (("torch",), {}, "DEPENDENCY_FORBIDDEN"),
        (("scipy",), {"sandbox_dependency_allowlist": "scipy"}, "DEPENDENCY_FORBIDDEN"),
        (
            ("python-docx", "openpyxl", "pypdf", "markdown", "pillow", "numpy", "pandas"),
            {},
            "DEPENDENCY_TOO_MANY",
        ),
        (
            ("python-docx",),
            {"sandbox_network_enabled": False, "sandbox_dependency_from_cache": False},
            "DEPENDENCY_OFFLINE",
        ),
        (("python-docx",), {"sandbox_dependencies_enabled": False}, "DEPENDENCIES_DISABLED"),
        (("",), {}, "DEPENDENCY_INVALID"),
    ],
)
def test_dependency_plan_rejects_outside_policy(deps, overrides, code) -> None:
    settings = make_settings(**overrides)
    with pytest.raises(dep_policy.DependencyRejected) as caught:
        dep_policy.plan(deps, settings)
    assert caught.value.code == code


def test_dependency_plan_accepts_allowlisted_versions() -> None:
    settings = make_settings()
    plan = dep_policy.plan(("python-docx>=1.1", "pypdf"), settings)
    assert plan.packages == ("python-docx>=1.1", "pypdf")
    # 体积是按「一次安装的总量」算的，不是每个包
    assert plan.max_bytes == 48 * 1024 * 1024
    assert plan.timeout == 45
    argv = dep_policy.pip_argv("python", plan, __import__("pathlib").Path("/tmp/deps"))
    # 只允许预编译轮子：没有 wheel 就失败，绝不触发本地编译
    assert "--only-binary=:all:" in argv
    assert "--target" in argv
    # 默认走预热缓存：完全不联网
    assert "--no-index" in argv
    assert "--find-links" in argv


def test_dependency_plan_can_fall_back_to_online_install() -> None:
    settings = make_settings(sandbox_dependency_from_cache=False)
    plan = dep_policy.plan(("pypdf",), settings)
    argv = dep_policy.pip_argv("python", plan, __import__("pathlib").Path("/tmp/deps"))
    assert "--no-index" not in argv


def test_media_packages_are_allowlisted() -> None:
    settings = make_settings()
    permitted = dep_policy.allowlist(settings)
    for name in ("pillow", "pydub", "mutagen", "ffmpeg-python", "qrcode"):
        assert name in permitted
    # 媒体包也能通过策略（体积按总量卡，不在这里）
    plan = dep_policy.plan(("pillow", "pydub", "mutagen"), settings)
    assert len(plan.packages) == 3


def test_heavy_wheels_stay_out_of_the_allowlist() -> None:
    """numpy/pandas/imageio 这类大轮子运行时装不动（实测 >60s），不该进白名单。"""
    settings = make_settings()
    permitted = dep_policy.allowlist(settings)
    for name in ("numpy", "pandas", "matplotlib", "imageio", "imageio-ffmpeg", "moviepy"):
        assert name not in permitted


def test_skill_declared_dependencies_skip_allowlist_but_keep_limits() -> None:
    settings = make_settings()
    # 技能清单声明的包不受白名单限制
    plan = dep_policy.plan(("some-vendor-pkg",), settings, trusted=True)
    assert plan.packages == ("some-vendor-pkg",)
    # 但硬禁令、数量上限照旧
    with pytest.raises(dep_policy.DependencyRejected) as forbidden:
        dep_policy.plan(("torch",), settings, trusted=True)
    assert forbidden.value.code == "DEPENDENCY_FORBIDDEN"
    with pytest.raises(dep_policy.DependencyRejected) as too_many:
        dep_policy.plan(tuple(f"pkg-{index}" for index in range(7)), settings, trusted=True)
    assert too_many.value.code == "DEPENDENCY_TOO_MANY"


# ---------- 工作区探测 ----------


def test_workspace_path_is_exposed_by_env_on_local_backend(tmp_path) -> None:
    settings = make_settings(tools_workspace_root=str(tmp_path), sandbox_backend="local")
    bridge = detect_workspace(settings)
    assert bridge.mode == "path"
    assert bridge.env[WORKSPACE_ENV] == str(tmp_path.resolve())
    text = bridge.describe()
    assert "MUZI_WORKSPACE" in text
    # 关键回归：local 下不能再承诺 /workspace 存在
    assert "挂载在 /workspace" not in text


def test_workspace_is_invisible_when_root_missing() -> None:
    settings = make_settings(tools_workspace_root="", sandbox_backend="local")
    bridge = detect_workspace(settings)
    assert bridge.mode == "none"
    assert not bridge.visible
    assert "看不到用户工作区" in bridge.describe()


def test_run_python_description_follows_the_actual_backend(tmp_path) -> None:
    token = CURRENT_SETTINGS.set(
        make_settings(tools_workspace_root=str(tmp_path), sandbox_backend="local")
    )
    try:
        text = sandbox_tool_description("run_python")
    finally:
        CURRENT_SETTINGS.reset(token)
    assert WORKSPACE_ENV in text
    assert "/workspace" not in text
    assert "dependencies" in text
    assert "python-docx" in text


# ---------- 超时保留输出 ----------


def test_run_python_timeout_keeps_partial_output(tmp_path) -> None:
    token = CURRENT_SETTINGS.set(
        make_settings(
            tools_workspace_root=str(tmp_path),
            tools_sandbox_timeout_seconds=1,
        )
    )
    try:
        payload = json.loads(
            run_python(PythonArgs(code="print('step-one')\nimport time\ntime.sleep(30)\n"))
        )
    finally:
        CURRENT_SETTINGS.reset(token)
    assert payload["error"]["code"] == "code_timeout"
    assert payload.get("partial") is True
    assert "step-one" in payload["output"]


def test_run_python_accepts_a_longer_timeout_within_the_cap(tmp_path) -> None:
    token = CURRENT_SETTINGS.set(
        make_settings(tools_workspace_root=str(tmp_path), tools_sandbox_timeout_seconds=1)
    )
    try:
        payload = json.loads(
            run_python(PythonArgs(code="import time\ntime.sleep(1.5)\nprint('done')\n", timeout_seconds=10))
        )
    finally:
        CURRENT_SETTINGS.reset(token)
    assert payload["exit_code"] == 0
    assert "done" in payload["output"]


def test_run_python_requests_a_budget_within_the_configured_cap(tmp_path, monkeypatch) -> None:
    """请求值必须被 tools_sandbox_timeout_max 截断，不能靠模型自己守规矩。"""
    captured = {}

    class FakeProvider:
        def run(self, spec):
            captured["timeout"] = spec.timeout
            captured["argv"] = list(spec.argv)
            from app.tools.sandbox.provider import SandboxResult

            return SandboxResult(exit_code=0, output="", truncated=False)

    monkeypatch.setattr("app.tools.code_runner.get_provider", lambda _settings: FakeProvider())
    token = CURRENT_SETTINGS.set(
        make_settings(tools_workspace_root=str(tmp_path), tools_sandbox_timeout_max=30)
    )
    try:
        run_python(PythonArgs(code="print(1)", timeout_seconds=60))
        assert captured["timeout"] == 30
        # 无依赖：保持 -I 收紧
        assert "-I" in captured["argv"]

        run_python(PythonArgs(code="print(1)", dependencies=["pypdf"]))
        # 有依赖：必须让 PYTHONPATH 生效，改用 -s
        assert "-I" not in captured["argv"]
        assert "-s" in captured["argv"]
    finally:
        CURRENT_SETTINGS.reset(token)


def test_offline_sandbox_injects_fast_fail_network_env(tmp_path) -> None:
    from app.tools.sandbox.workspace_bridge import sandbox_env

    settings = make_settings(tools_workspace_root=str(tmp_path), sandbox_network_enabled=False)
    bridge = detect_workspace(settings)
    env = sandbox_env(settings, bridge, offline=True)
    assert env["PIP_NO_INDEX"] == "1"
    assert env["HTTP_PROXY"].startswith("http://127.0.0.1:")
    assert env[WORKSPACE_ENV] == str(tmp_path.resolve())
