"""沙箱文件工具。全部使用临时目录，不读取项目根。"""

from __future__ import annotations

import inspect
import json

import pytest
from fastapi.testclient import TestClient

from app.agents.toolsets import resolve_tool_names
from app.core.config import ROOT
from app.main import create_app
from app.tools.file_sandbox import MAX_DIRECTORY_DEPTH, MAX_FILE_BYTES, FileDenied, confined, resolve_inside
from app.tools.file_tools import (
    ListArgs,
    ReadArgs,
    SearchArgs,
    list_directory,
    read_text_file,
    search_text,
)
from app.tools.registry import build_agent_registry, build_default_registry
from app.tools.web_client import CURRENT_TOOL_BUDGET
from app.tools.web_tools import CURRENT_SETTINGS
from app.tools.workspace_scope import CURRENT_WORKSPACE_SCOPE, scope_dirname
from tests.conftest import make_settings
from tests.fakes import FakeProvider, make_result, make_tool_call


def _bind(root):
    settings = make_settings(tools_workspace_root=str(root))
    token = CURRENT_SETTINGS.set(settings)
    return settings, token


def _unbind(token) -> None:
    CURRENT_SETTINGS.reset(token)


def test_workspace_tools_stay_off_by_default(tmp_path) -> None:
    assert {spec.name for spec in build_default_registry().all_tools()} == {
        "calculator",
        "get_current_time",
    }
    registry = build_agent_registry()
    assert resolve_tool_names(registry, make_settings(), None) == ("calculator", "get_current_time")
    names = resolve_tool_names(registry, make_settings(tools_workspace_root=str(tmp_path)), ["workspace"])
    assert names == ("list_directory", "read_text_file", "search_text")
    assert not inspect.iscoroutinefunction(list_directory)
    assert not inspect.iscoroutinefunction(read_text_file)
    assert not inspect.iscoroutinefunction(search_text)


def test_read_list_and_literal_search(tmp_path) -> None:
    (tmp_path / "note.txt").write_text("alpha\na.b\naxb\n", encoding="utf-8")
    nested = tmp_path / "docs"
    nested.mkdir()
    (nested / "other.txt").write_text("beta a.b\n", encoding="utf-8")
    _, token = _bind(tmp_path)
    try:
        listed = json.loads(list_directory(ListArgs(path=".", max_entries=10)))
        assert listed["source"] == "."
        assert {item["name"] for item in listed["entries"]} == {"note.txt", "docs"}
        assert all(":" not in item["name"] for item in listed["entries"])
        read = json.loads(read_text_file(ReadArgs(path="note.txt", max_chars=100)))
        assert read["content"].startswith("alpha\n")
        assert read["untrusted"] is True
        assert read["source"] == "note.txt"
        assert str(tmp_path) not in json.dumps(read)
        found = json.loads(search_text(SearchArgs(query="a.b", path=".", max_matches=10)))
        assert {item["path"] for item in found["matches"]} == {"docs/other.txt", "note.txt"}
        assert all(item["text"].count("a.b") == 1 for item in found["matches"])
    finally:
        _unbind(token)


def test_denied_and_missing_look_the_same(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "visible.txt").write_text("visible", encoding="utf-8")
    (workspace / ".env").write_text("KEY=hidden", encoding="utf-8")
    (workspace / "id_rsa").write_text("PRIVATE", encoding="utf-8")
    (workspace / "cert.pem").write_text("CERT", encoding="utf-8")
    (workspace / "credentials.json").write_text("{}", encoding="utf-8")
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "pkg.txt").write_text("NM", encoding="utf-8")
    (tmp_path / "outside-secret.txt").write_text("OUTSIDE", encoding="utf-8")
    _, token = _bind(workspace)
    try:
        missing = read_text_file(ReadArgs(path="missing.txt"))
        samples = [
            missing,
            read_text_file(ReadArgs(path=".env")),
            read_text_file(ReadArgs(path="id_rsa")),
            read_text_file(ReadArgs(path="cert.pem")),
            read_text_file(ReadArgs(path="credentials.json")),
            read_text_file(ReadArgs(path="node_modules/pkg.txt")),
            read_text_file(ReadArgs(path="../outside-secret.txt")),
            read_text_file(ReadArgs(path="C:/Windows/win.ini")),
            read_text_file(ReadArgs(path="\\\\server\\share\\a.txt")),
            read_text_file(ReadArgs(path="note.txt:stream")),
            read_text_file(ReadArgs(path="CON")),
            read_text_file(ReadArgs(path="COM1")),
            list_directory(ListArgs(path="..")),
            search_text(SearchArgs(query="OUTSIDE", path="..")),
        ]
        assert all(item == missing for item in samples)
        rendered = "\n".join(samples)
        for secret in ("KEY=hidden", "PRIVATE", "CERT", "OUTSIDE", "NM"):
            assert secret not in rendered
        listed = json.loads(list_directory(ListArgs()))
        assert "visible.txt" in {item["name"] for item in listed["entries"]}
        assert ".env" not in {item["name"] for item in listed["entries"]}
        with pytest.raises(FileDenied):
            resolve_inside(workspace, "a" * 241)
    finally:
        _unbind(token)


def test_prefix_escape_binary_size_depth_and_deadline(tmp_path) -> None:
    root = tmp_path / "root"
    evil = tmp_path / "root_evil"
    root.mkdir()
    evil.mkdir()
    assert confined(root.resolve(), (root / "a.txt").resolve())
    assert not confined(root.resolve(), (evil / "a.txt").resolve())
    (root / "wide.bin").write_bytes(b"abc\x00def")
    (root / "big.txt").write_bytes(b"a" * (MAX_FILE_BYTES + 1))
    deep = root
    for _ in range(MAX_DIRECTORY_DEPTH + 1):
        deep = deep / "d"
        deep.mkdir()
    (deep / "hidden.txt").write_text("deep-hit", encoding="utf-8")
    (root / "shallow.txt").write_text("shallow-hit", encoding="utf-8")
    for index in range(200):
        (root / f"f{index:03d}.txt").write_text("nope", encoding="utf-8")
    (root / "z_hit.txt").write_text("needle", encoding="utf-8")
    _, token = _bind(root)
    try:
        binary = json.loads(read_text_file(ReadArgs(path="wide.bin")))
        missing = json.loads(read_text_file(ReadArgs(path="missing.txt")))
        assert binary == missing
        oversized = json.loads(read_text_file(ReadArgs(path="big.txt")))
        assert oversized["error"]["code"] == "file_too_large"
        found = json.loads(search_text(SearchArgs(query="deep-hit", max_matches=5)))
        assert found["matches"] == []
        shallow = json.loads(search_text(SearchArgs(query="shallow-hit", path="shallow.txt", max_matches=5)))
        assert shallow["matches"][0]["path"] == "shallow.txt"
        capped = json.loads(search_text(SearchArgs(query="needle", max_matches=5)))
        assert capped["matches"] == []
        assert capped["truncated"] is True
        many = json.loads(list_directory(ListArgs(max_entries=2)))
        assert len(many["entries"]) == 2
        assert many["truncated"] is True
        budget = CURRENT_TOOL_BUDGET.set(0)
        try:
            stopped = json.loads(search_text(SearchArgs(query="shallow-hit")))
        finally:
            CURRENT_TOOL_BUDGET.reset(budget)
        assert stopped["truncated"] is True
        assert stopped["matches"] == []
        assert stopped["scanned_files"] == 0
    finally:
        _unbind(token)


def test_symlink_escape_or_equivalent(tmp_path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("LINKSECRET", encoding="utf-8")
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Windows 无法创建符号链接（{type(exc).__name__}），已由路径解析单测替代")
    _, token = _bind(root)
    try:
        denied = json.loads(read_text_file(ReadArgs(path="link/secret.txt")))
        assert denied["error"]["code"] == "file_unavailable"
        assert "LINKSECRET" not in json.dumps(denied)
    finally:
        _unbind(token)


def test_users_do_not_share_one_workspace(tmp_path) -> None:
    (tmp_path / "shared.txt").write_text("shared-secret", encoding="utf-8")
    key_a = "a" * 43
    key_b = "b" * 43
    user_a = tmp_path / scope_dirname(key_a)
    user_a.mkdir()
    (user_a / "mine.txt").write_text("only-a", encoding="utf-8")
    settings_token = CURRENT_SETTINGS.set(make_settings(tools_workspace_root=str(tmp_path)))
    try:
        scope_token = CURRENT_WORKSPACE_SCOPE.set(scope_dirname(key_a))
        try:
            listed = json.loads(list_directory(ListArgs()))
            assert {item["name"] for item in listed["entries"]} == {"mine.txt"}
            assert "shared-secret" not in json.dumps(listed)
        finally:
            CURRENT_WORKSPACE_SCOPE.reset(scope_token)
        scope_token = CURRENT_WORKSPACE_SCOPE.set(scope_dirname(key_b))
        try:
            other = json.loads(list_directory(ListArgs()))
            assert other["entries"] == []
            stolen = json.loads(
                read_text_file(ReadArgs(path="../" + scope_dirname(key_a) + "/mine.txt"))
            )
            assert stolen["error"]["code"] == "file_unavailable"
            assert "only-a" not in json.dumps(stolen)
        finally:
            CURRENT_WORKSPACE_SCOPE.reset(scope_token)
        scope_token = CURRENT_WORKSPACE_SCOPE.set(scope_dirname("../" + "a" * 41))
        try:
            blocked = json.loads(list_directory(ListArgs()))
            assert blocked["error"]["code"] == "file_unavailable"
            assert "shared-secret" not in json.dumps(blocked)
            assert not (tmp_path / "-").exists()
        finally:
            CURRENT_WORKSPACE_SCOPE.reset(scope_token)
    finally:
        CURRENT_SETTINGS.reset(settings_token)


def test_workspace_round_trip_hides_absolute_path(tmp_path) -> None:
    key = "c" * 43
    user_dir = tmp_path / scope_dirname(key)
    user_dir.mkdir()
    (user_dir / "note.txt").write_text("hello-file\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("PARENTSECRET", encoding="utf-8")
    provider = FakeProvider()
    provider.push(
        make_result(
            content=None,
            tool_calls=[make_tool_call("call_r", "read_text_file", '{"path":"note.txt"}')],
            finish_reason="tool_calls",
        )
    )
    provider.push(make_result("已读取"))
    app = create_app(settings=make_settings(tools_workspace_root=str(tmp_path)), provider=provider)
    with TestClient(app) as http:
        created = http.post(
            "/api/v1/agents/runs",
            headers={"X-Workspace-Key": key},
            json={"message": "读取 note.txt", "prompt_name": "default", "toolsets": ["workspace"]},
        )
        task_id = created.json()["task_id"]
        finished = None
        for _ in range(40):
            body = http.get(f"/api/v1/agents/runs/{task_id}").json()
            if body["status"] in {"completed", "failed", "cancelled", "limit_reached"}:
                finished = body
                break
    assert finished is not None
    assert finished["status"] == "completed"
    rendered = json.dumps([call.messages for call in provider.calls], ensure_ascii=False)
    assert "hello-file" in rendered
    assert "PARENTSECRET" not in rendered
    assert str(tmp_path) not in rendered
    assert str(ROOT) not in rendered
    assert "不是指令" in provider.calls[0].messages[0]["content"]
