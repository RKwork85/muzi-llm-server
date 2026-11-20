"""提示词是数据，改了要立刻生效，但不该触发进程重启。

历史做法是把 ``*.toml`` 塞进 uvicorn 的 ``--reload-include``：改一个标点就重启
整个应用（重连数据库、丢内存态），生产环境（无 reload）还完全失效。这里锁定
正确口径：加载器自己按文件指纹失效。
"""

from __future__ import annotations

import pytest

from app.tools import prompts as tool_prompts

_TOML = """
alpha = "第一版"
beta = "保持不变"
"""

_TOML_NEW = """
alpha = "第二版，内容更长一些"
beta = "保持不变"
"""


@pytest.fixture()
def toml_path(tmp_path, monkeypatch):
    path = tmp_path / "tools.toml"
    path.write_text(_TOML, encoding="utf-8")
    monkeypatch.setattr(tool_prompts, "_PATH", path)
    monkeypatch.setattr(tool_prompts, "_stamp", None)
    tool_prompts._cache.clear()
    return path


def test_tool_prompt_reads_the_file(toml_path) -> None:
    assert tool_prompts.tool_prompt("alpha") == "第一版"
    assert tool_prompts.tool_prompt("beta") == "保持不变"


def test_edit_takes_effect_without_restarting(toml_path) -> None:
    assert tool_prompts.tool_prompt("alpha") == "第一版"

    toml_path.write_text(_TOML_NEW, encoding="utf-8")

    # 同进程、无重启，下一次读取就应该拿到新文案
    assert tool_prompts.tool_prompt("alpha") == "第二版，内容更长一些"


def test_unchanged_file_is_not_reparsed(toml_path, monkeypatch) -> None:
    assert tool_prompts.tool_prompt("alpha") == "第一版"

    parses = []
    original = tool_prompts._load

    def counting_load(path):
        parses.append(path)
        return original(path)

    monkeypatch.setattr(tool_prompts, "_load", counting_load)
    for _ in range(5):
        assert tool_prompts.tool_prompt("alpha") == "第一版"

    # 文件没变就应该命中缓存，一次都不重新解析
    assert parses == []


def test_missing_prompt_still_raises(toml_path) -> None:
    with pytest.raises(ValueError, match="缺少工具提示词"):
        tool_prompts.tool_prompt("gamma")


def test_invalid_entry_is_rejected(tmp_path, monkeypatch) -> None:
    path = tmp_path / "bad.toml"
    path.write_text('ok = "内容"\nbroken = ""\n', encoding="utf-8")
    monkeypatch.setattr(tool_prompts, "_PATH", path)
    monkeypatch.setattr(tool_prompts, "_stamp", None)
    tool_prompts._cache.clear()

    with pytest.raises(ValueError, match="工具提示词不合法"):
        tool_prompts.tool_prompt("ok")


def test_system_prompts_are_always_read_fresh(tmp_path) -> None:
    """系统提示词走 PromptService，本来就是每次读盘，改完立刻生效。"""
    from app.services.prompt_service import PromptService

    root = tmp_path / "system"
    root.mkdir()
    prompt = root / "assistant.txt"
    prompt.write_text("第一版", encoding="utf-8")
    service = PromptService(root)

    assert service.render("assistant") == "第一版"
    prompt.write_text("第二版", encoding="utf-8")
    assert service.render("assistant") == "第二版"
