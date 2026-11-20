"""沙盒产物通道：唯一可写出口、工作区只读快照、交付定位。

这三条是同一条链路上的三次事故点，回归时必须一起测：
1. 沙盒里能写二进制（模型不再被迫用文本冒充 .xmind）；
2. 工作区对沙盒只读（写副本不影响真实文件）；
3. deliver_file 能找到 _out 里的产物（工单里「生成成功却交付失败」就是这一步断的）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.tools.code_runner import PythonArgs, run_python
from app.tools.deliver_file import _locate
from app.tools.file_changes import WriteArgs, write_text_file
from app.tools.file_sandbox import FileDenied
from app.tools.sandbox.outputs import OUTPUT_DIRNAME, OUTPUT_ENV, output_dir
from app.tools.sandbox.workspace_bridge import WORKSPACE_ENV, detect_workspace
from app.tools.web_tools import CURRENT_SETTINGS
from app.tools.workspace_scope import CURRENT_WORKSPACE_SCOPE
from tests.conftest import make_settings


@pytest.fixture()
def sandbox_settings(tmp_path):
    token = CURRENT_SETTINGS.set(
        make_settings(tools_workspace_root=str(tmp_path), sandbox_backend="local", tools_sandbox_timeout_seconds=10)
    )
    try:
        yield tmp_path
    finally:
        CURRENT_SETTINGS.reset(token)


# ---------- 产物出口 ----------


def test_binary_product_is_written_to_the_output_dir(sandbox_settings) -> None:
    """run_python 写进 MUZI_OUTPUT 的二进制会保留下来，并在结果里点名。"""
    code = """
import io, os, zipfile
out = os.path.join(os.environ["MUZI_OUTPUT"], "products.xmind")
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as package:
    package.writestr("content.json", '[{"class": "sheet"}]')
with open(out, "wb") as handle:
    handle.write(buf.getvalue())
print("ok", len(buf.getvalue()))
"""
    payload = json.loads(run_python(PythonArgs(code=code)))

    assert payload["exit_code"] == 0
    assert payload["outputs"][0]["file_name"] == "products.xmind"
    assert payload["outputs"][0]["path"] == f"{OUTPUT_DIRNAME}/products.xmind"
    produced = sandbox_settings / OUTPUT_DIRNAME / "products.xmind"
    assert produced.is_file()
    # 真正是 ZIP，不是文本冒充
    assert produced.read_bytes()[:4] == b"PK\x03\x04"


def test_outputs_are_reported_only_when_new(sandbox_settings) -> None:
    """第二次执行没有新产物时不该再报一遍旧文件。"""
    first = json.loads(
        run_python(
            PythonArgs(
                code='import os\nopen(os.path.join(os.environ["MUZI_OUTPUT"], "one.txt"), "w").write("1")\n'
            )
        )
    )
    second = json.loads(run_python(PythonArgs(code="print('nothing new')")))

    assert first["outputs"][0]["file_name"] == "one.txt"
    assert "outputs" not in second


# ---------- 工作区只读快照 ----------


def test_workspace_writes_do_not_touch_the_real_workspace(sandbox_settings) -> None:
    original = sandbox_settings / "real.txt"
    original.write_text("original", encoding="utf-8")

    code = """
import os
root = os.environ["MUZI_WORKSPACE"]
open(os.path.join(root, "real.txt"), "w").write("hacked")
open(os.path.join(root, "new.txt"), "w").write("created")
print(os.path.abspath(root))
"""
    payload = json.loads(run_python(PythonArgs(code=code)))

    assert payload["exit_code"] == 0
    # 真实工作区一个字节都没变，也没有被塞进新文件
    assert original.read_text(encoding="utf-8") == "original"
    assert not (sandbox_settings / "new.txt").exists()


def test_workspace_env_follows_the_user_scope(tmp_path) -> None:
    """沙盒看到的路径必须和 deliver_file 一样带上 workspace scope。"""
    scope = "a" * 32
    settings = make_settings(tools_workspace_root=str(tmp_path), sandbox_backend="local")
    token = CURRENT_WORKSPACE_SCOPE.set(scope)
    try:
        bridge = detect_workspace(settings)
    finally:
        CURRENT_WORKSPACE_SCOPE.reset(token)

    assert bridge.mode == "path"
    assert bridge.env[WORKSPACE_ENV].endswith(scope)
    assert Path(bridge.env[OUTPUT_ENV]).name == OUTPUT_DIRNAME
    assert Path(bridge.env[OUTPUT_ENV]).parent.name == scope
    assert Path(bridge.output) == Path(bridge.env[OUTPUT_ENV])


# ---------- 交付定位 ----------


def test_deliver_file_locates_products_in_the_output_dir(tmp_path) -> None:
    directory = output_dir(make_settings(tools_workspace_root=str(tmp_path)))
    assert directory is not None
    product = directory / "products.xmind"
    product.write_bytes(b"PK\x03\x04")

    # 显式写 _out/ 前缀、或只写文件名，都要能找到
    assert _locate(tmp_path, f"{OUTPUT_DIRNAME}/products.xmind") == product
    assert _locate(tmp_path, "products.xmind") == product
    with pytest.raises(FileDenied):
        _locate(tmp_path, "missing.xmind")


# ---------- 不再允许文本冒充二进制 ----------


@pytest.mark.parametrize("suffix", [".xmind", ".zip", ".png", ".mp4"])
def test_text_writer_rejects_binary_suffixes(sandbox_settings, suffix) -> None:
    result = json.loads(write_text_file(WriteArgs(path=f"fake{suffix}", content='{"a": 1}')))

    assert result["error"]["code"] == "file_unavailable"
    assert "二进制" in result["error"]["message"]
    assert not (sandbox_settings / f"fake{suffix}").exists()
