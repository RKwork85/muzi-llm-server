"""飞书（Lark）OpenAPI 桥接。凭据按用户隔离，tenant_access_token 按用户进程内缓存。

覆盖常用接口：消息、通讯录、云文档、云空间、日历、任务。
全部走 tenant_access_token（应用身份），凭据是用户自己在飞书开放平台创建的自建应用。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import httpx

BASE = "https://open.feishu.cn/open-apis"
_TOKEN_TTL = 7000  # 官方 7200 秒，提前 200 秒刷新

_lock = asyncio.Lock()
# user_id -> {"token": str, "expires": float}
_cached: dict[str, dict[str, Any]] = {}

_RECEIVE_ID_TYPES = ("open_id", "user_id", "email", "chat_id")
_USER_ID_TYPES = ("open_id", "union_id", "user_id")


def configured(credentials: dict[str, Any]) -> bool:
    return bool(str(credentials.get("app_id", "")).strip() and str(credentials.get("app_secret", "")).strip())


def tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "send_text",
            "description": (
                "向飞书用户或群发送文本消息。receive_id 为接收者标识，"
                "receive_id_type 可选 open_id / user_id / email / chat_id（默认 open_id）。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "receive_id": {"type": "string", "description": "接收者标识"},
                    "content": {"type": "string", "description": "消息文本内容"},
                    "receive_id_type": {"type": "string", "description": "open_id / user_id / email / chat_id"},
                },
                "required": ["receive_id", "content"],
            },
        },
        {
            "name": "get_user",
            "description": (
                "查询飞书通讯录里的单个用户信息（姓名、邮箱、手机号、部门等）。"
                "user_id 为标识，user_id_type 可选 open_id / union_id / user_id（默认 open_id）。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "用户标识"},
                    "user_id_type": {"type": "string", "description": "open_id / union_id / user_id"},
                },
                "required": ["user_id"],
            },
        },
        {
            "name": "list_departments",
            "description": (
                "列出飞书通讯录部门。parent_department_id 为父部门 open_department_id，"
                "不传或传 0 表示根部门。返回第一页（最多 50 条）。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "parent_department_id": {"type": "string", "description": "父部门 open_department_id，默认 0"},
                    "page_size": {"type": "integer", "description": "每页条数，默认 50，最大 50"},
                },
                "required": [],
            },
        },
        {
            "name": "list_department_users",
            "description": (
                "列出指定飞书部门下的直属用户。department_id 为部门 open_department_id。"
                "返回第一页（最多 50 条）。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "department_id": {"type": "string", "description": "部门 open_department_id"},
                    "page_size": {"type": "integer", "description": "每页条数，默认 50，最大 50"},
                },
                "required": ["department_id"],
            },
        },
        {
            "name": "create_doc",
            "description": (
                "在飞书创建一篇云文档（docx）并写入正文。title 为文档标题；"
                "content 为正文，行首 # / ## / ### 会转成对应级别的标题，其余行作为正文段落。"
                "folder_token 可选，为云空间文件夹 token，不传则创建在「我的空间」根目录。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "文档标题"},
                    "content": {"type": "string", "description": "文档正文，支持 # 标题行"},
                    "folder_token": {"type": "string", "description": "可选，云空间文件夹 token"},
                },
                "required": ["title", "content"],
            },
        },
        {
            "name": "get_doc_content",
            "description": "读取一篇飞书云文档的纯文本内容。document_id 为文档标识。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "文档 document_id"},
                },
                "required": ["document_id"],
            },
        },
        {
            "name": "create_folder",
            "description": (
                "在飞书云空间创建文件夹。name 为文件夹名；folder_token 可选，为父文件夹 token，"
                "不传则创建在云空间根目录。返回新文件夹 token。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "文件夹名称"},
                    "folder_token": {"type": "string", "description": "可选，父文件夹 token"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "list_files",
            "description": (
                "列出飞书云空间某个文件夹下的文件与子文件夹。folder_token 可选，"
                "不传表示根目录。返回第一页（最多 50 条）。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "folder_token": {"type": "string", "description": "可选，文件夹 token，不传为根目录"},
                    "page_size": {"type": "integer", "description": "每页条数，默认 50，最大 50"},
                },
                "required": [],
            },
        },
        {
            "name": "create_event",
            "description": (
                "在指定飞书日历创建日程。calendar_id 为日历 ID（需用户提供，不能填 primary）；"
                "summary 为日程标题；start_timestamp / end_timestamp 为秒级时间戳。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "calendar_id": {"type": "string", "description": "日历 ID"},
                    "summary": {"type": "string", "description": "日程标题"},
                    "description": {"type": "string", "description": "日程说明，可选"},
                    "start_timestamp": {"type": "integer", "description": "开始时间（秒级时间戳）"},
                    "end_timestamp": {"type": "integer", "description": "结束时间（秒级时间戳）"},
                },
                "required": ["calendar_id", "summary", "start_timestamp", "end_timestamp"],
            },
        },
        {
            "name": "list_events",
            "description": (
                "查询指定飞书日历在某时间段内的日程。calendar_id 为日历 ID；"
                "start_timestamp / end_timestamp 为秒级时间戳，可选，不传则查询默认范围。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "calendar_id": {"type": "string", "description": "日历 ID"},
                    "start_timestamp": {"type": "integer", "description": "开始时间（秒级时间戳），可选"},
                    "end_timestamp": {"type": "integer", "description": "结束时间（秒级时间戳），可选"},
                },
                "required": ["calendar_id"],
            },
        },
        {
            "name": "create_task",
            "description": (
                "在飞书任务创建一条任务。summary 为任务标题；description 为说明，可选；"
                "due_timestamp 为截止时间（秒级时间戳），可选。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "任务标题"},
                    "description": {"type": "string", "description": "任务说明，可选"},
                    "due_timestamp": {"type": "integer", "description": "截止时间（秒级时间戳），可选"},
                },
                "required": ["summary"],
            },
        },
    ]


async def _tenant_token(user_id: str, credentials: dict[str, Any], timeout: float) -> str:
    now = time.monotonic()
    cached = _cached.get(user_id)
    if cached and cached.get("token") and cached["expires"] > now:
        return cached["token"]
    async with _lock:
        now = time.monotonic()
        cached = _cached.get(user_id)
        if cached and cached.get("token") and cached["expires"] > now:
            return cached["token"]
        payload = {"app_id": credentials["app_id"], "app_secret": credentials["app_secret"]}
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request("POST", f"{BASE}/auth/v3/tenant_access_token/internal", json=payload)
        body = _json(response)
        if body.get("code") != 0:
            raise RuntimeError(f"feishu tenant token failed: {body.get('msg')}")
        token = body.get("tenant_access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("feishu tenant token missing")
        _cached[user_id] = {"token": token, "expires": now + _TOKEN_TTL}
        return token


async def _call(user_id: str, credentials: dict[str, Any], timeout: float, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    """带 tenant token 的通用请求。code != 0 时抛出含飞书 msg 的异常。"""
    token = await _tenant_token(user_id, credentials, timeout)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(method, url, headers=headers, **kwargs)
    body = _json(response)
    if body.get("code") != 0:
        raise RuntimeError(f"feishu api failed: {body.get('msg')}")
    return body


def _page_size(arguments: dict[str, Any]) -> int:
    raw = arguments.get("page_size")
    if isinstance(raw, int) and 1 <= raw <= 50:
        return raw
    return 50


async def call_tool(user_id: str, credentials: dict[str, Any], timeout: float, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        if name == "send_text":
            text = await _send_text(user_id, credentials, timeout, arguments)
        elif name == "get_user":
            text = await _get_user(user_id, credentials, timeout, arguments)
        elif name == "list_departments":
            text = await _list_departments(user_id, credentials, timeout, arguments)
        elif name == "list_department_users":
            text = await _list_department_users(user_id, credentials, timeout, arguments)
        elif name == "create_doc":
            text = await _create_doc(user_id, credentials, timeout, arguments)
        elif name == "get_doc_content":
            text = await _get_doc_content(user_id, credentials, timeout, arguments)
        elif name == "create_folder":
            text = await _create_folder(user_id, credentials, timeout, arguments)
        elif name == "list_files":
            text = await _list_files(user_id, credentials, timeout, arguments)
        elif name == "create_event":
            text = await _create_event(user_id, credentials, timeout, arguments)
        elif name == "list_events":
            text = await _list_events(user_id, credentials, timeout, arguments)
        elif name == "create_task":
            text = await _create_task(user_id, credentials, timeout, arguments)
        else:
            return _error(f"未知工具 {name}")
        return _ok(text)
    except Exception as exc:
        return _error(str(exc))


async def _send_text(user_id: str, credentials: dict[str, Any], timeout: float, arguments: dict[str, Any]) -> str:
    receive_id = str(arguments.get("receive_id", "")).strip()
    content = str(arguments.get("content", "")).strip()
    if not receive_id or not content:
        raise ValueError("receive_id 和 content 不能为空")
    receive_id_type = str(arguments.get("receive_id_type", "open_id")).strip() or "open_id"
    if receive_id_type not in _RECEIVE_ID_TYPES:
        raise ValueError("receive_id_type 不合法")
    body = await _call(
        user_id,
        credentials,
        timeout,
        "POST",
        f"{BASE}/im/v1/messages",
        params={"receive_id_type": receive_id_type},
        json={"receive_id": receive_id, "msg_type": "text", "content": json.dumps({"text": content}, ensure_ascii=False)},
    )
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    return json.dumps({"sent": True, "message_id": data.get("message_id")}, ensure_ascii=False)


async def _get_user(user_id: str, credentials: dict[str, Any], timeout: float, arguments: dict[str, Any]) -> str:
    target = str(arguments.get("user_id", "")).strip()
    if not target:
        raise ValueError("user_id 不能为空")
    user_id_type = str(arguments.get("user_id_type", "open_id")).strip() or "open_id"
    if user_id_type not in _USER_ID_TYPES:
        raise ValueError("user_id_type 不合法")
    body = await _call(
        user_id,
        credentials,
        timeout,
        "GET",
        f"{BASE}/contact/v3/users/{target}",
        params={"user_id_type": user_id_type, "department_id_type": "open_department_id"},
    )
    user = body.get("data", {}).get("user") if isinstance(body.get("data"), dict) else None
    return json.dumps({"user": user}, ensure_ascii=False)


async def _list_departments(user_id: str, credentials: dict[str, Any], timeout: float, arguments: dict[str, Any]) -> str:
    parent = str(arguments.get("parent_department_id", "0")).strip() or "0"
    body = await _call(
        user_id,
        credentials,
        timeout,
        "GET",
        f"{BASE}/contact/v3/departments",
        params={"parent_department_id": parent, "page_size": _page_size(arguments), "department_id_type": "open_department_id"},
    )
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    return json.dumps(
        {"departments": data.get("items") or [], "has_more": data.get("has_more", False)},
        ensure_ascii=False,
    )


async def _list_department_users(user_id: str, credentials: dict[str, Any], timeout: float, arguments: dict[str, Any]) -> str:
    department = str(arguments.get("department_id", "")).strip()
    if not department:
        raise ValueError("department_id 不能为空")
    body = await _call(
        user_id,
        credentials,
        timeout,
        "GET",
        f"{BASE}/contact/v3/users/find_by_department",
        params={
            "department_id": department,
            "page_size": _page_size(arguments),
            "user_id_type": "open_id",
            "department_id_type": "open_department_id",
        },
    )
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    return json.dumps(
        {"users": data.get("items") or [], "has_more": data.get("has_more", False)},
        ensure_ascii=False,
    )


async def _create_doc(user_id: str, credentials: dict[str, Any], timeout: float, arguments: dict[str, Any]) -> str:
    title = str(arguments.get("title", "")).strip()
    content = str(arguments.get("content", "")).strip()
    if not title:
        raise ValueError("title 不能为空")
    if not content:
        raise ValueError("content 不能为空")
    folder_token = str(arguments.get("folder_token", "")).strip()
    payload: dict[str, Any] = {"title": title}
    if folder_token:
        payload["folder_token"] = folder_token
    body = await _call(user_id, credentials, timeout, "POST", f"{BASE}/docx/v1/documents", json=payload)
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    document = data.get("document") if isinstance(data.get("document"), dict) else {}
    document_id = document.get("document_id")
    if not isinstance(document_id, str) or not document_id:
        raise RuntimeError("feishu create doc missing document_id")
    blocks = _content_blocks(content)
    if blocks:
        await _call(
            user_id,
            credentials,
            timeout,
            "POST",
            f"{BASE}/docx/v1/documents/{document_id}/blocks/{document_id}/children",
            json={"children": blocks, "index": 0},
        )
    return json.dumps(
        {"created": True, "document_id": document_id, "url": f"https://feishu.cn/docx/{document_id}"},
        ensure_ascii=False,
    )


async def _get_doc_content(user_id: str, credentials: dict[str, Any], timeout: float, arguments: dict[str, Any]) -> str:
    document_id = str(arguments.get("document_id", "")).strip()
    if not document_id:
        raise ValueError("document_id 不能为空")
    body = await _call(user_id, credentials, timeout, "GET", f"{BASE}/docx/v1/documents/{document_id}/raw_content")
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    return json.dumps({"content": data.get("content", "")}, ensure_ascii=False)


async def _create_folder(user_id: str, credentials: dict[str, Any], timeout: float, arguments: dict[str, Any]) -> str:
    name = str(arguments.get("name", "")).strip()
    if not name:
        raise ValueError("name 不能为空")
    payload: dict[str, Any] = {"name": name}
    folder_token = str(arguments.get("folder_token", "")).strip()
    if folder_token:
        payload["folder_token"] = folder_token
    body = await _call(user_id, credentials, timeout, "POST", f"{BASE}/drive/v1/files/create_folder", json=payload)
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    return json.dumps({"created": True, "folder_token": data.get("token")}, ensure_ascii=False)


async def _list_files(user_id: str, credentials: dict[str, Any], timeout: float, arguments: dict[str, Any]) -> str:
    params: dict[str, Any] = {"page_size": _page_size(arguments)}
    folder_token = str(arguments.get("folder_token", "")).strip()
    if folder_token:
        params["folder_token"] = folder_token
    body = await _call(user_id, credentials, timeout, "GET", f"{BASE}/drive/v1/files", params=params)
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    return json.dumps(
        {"files": data.get("files") or [], "has_more": data.get("has_more", False)},
        ensure_ascii=False,
    )


async def _create_event(user_id: str, credentials: dict[str, Any], timeout: float, arguments: dict[str, Any]) -> str:
    calendar_id = str(arguments.get("calendar_id", "")).strip()
    summary = str(arguments.get("summary", "")).strip()
    start_ts = arguments.get("start_timestamp")
    end_ts = arguments.get("end_timestamp")
    if not calendar_id or not summary:
        raise ValueError("calendar_id 和 summary 不能为空")
    if not isinstance(start_ts, int) or not isinstance(end_ts, int):
        raise ValueError("start_timestamp / end_timestamp 必须是秒级整数时间戳")
    payload: dict[str, Any] = {
        "summary": summary,
        "start_time": {"timestamp": str(start_ts)},
        "end_time": {"timestamp": str(end_ts)},
    }
    description = str(arguments.get("description", "")).strip()
    if description:
        payload["description"] = description
    body = await _call(user_id, credentials, timeout, "POST", f"{BASE}/calendar/v4/calendars/{calendar_id}/events", json=payload)
    event = body.get("data", {}).get("event") if isinstance(body.get("data"), dict) else None
    return json.dumps({"created": True, "event": event}, ensure_ascii=False)


async def _list_events(user_id: str, credentials: dict[str, Any], timeout: float, arguments: dict[str, Any]) -> str:
    calendar_id = str(arguments.get("calendar_id", "")).strip()
    if not calendar_id:
        raise ValueError("calendar_id 不能为空")
    params: dict[str, Any] = {}
    start_ts = arguments.get("start_timestamp")
    end_ts = arguments.get("end_timestamp")
    if isinstance(start_ts, int):
        params["start_time"] = str(start_ts)
    if isinstance(end_ts, int):
        params["end_time"] = str(end_ts)
    body = await _call(user_id, credentials, timeout, "GET", f"{BASE}/calendar/v4/calendars/{calendar_id}/events", params=params)
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    return json.dumps({"events": data.get("items") or []}, ensure_ascii=False)


async def _create_task(user_id: str, credentials: dict[str, Any], timeout: float, arguments: dict[str, Any]) -> str:
    summary = str(arguments.get("summary", "")).strip()
    if not summary:
        raise ValueError("summary 不能为空")
    payload: dict[str, Any] = {"summary": summary}
    description = str(arguments.get("description", "")).strip()
    if description:
        payload["description"] = description
    due_ts = arguments.get("due_timestamp")
    if isinstance(due_ts, int):
        payload["due"] = {"timestamp": str(due_ts)}
    body = await _call(user_id, credentials, timeout, "POST", f"{BASE}/task/v2/tasks", json=payload)
    task = body.get("data", {}).get("task") if isinstance(body.get("data"), dict) else None
    return json.dumps({"created": True, "task": task}, ensure_ascii=False)


_HEADING = re.compile(r"^(#{1,9})\s+(.+)$")


def _content_blocks(content: str) -> list[dict[str, Any]]:
    """把正文按行拆成飞书 docx 块。`#` 开头转标题块，其余转文本块，空行跳过。"""
    blocks: list[dict[str, Any]] = []
    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        match = _HEADING.match(line)
        if match:
            level = len(match.group(1))
            blocks.append(_heading_block(level, match.group(2).strip()))
        else:
            blocks.append(_text_block(line.strip()))
    return blocks


def _text_run(content: str) -> dict[str, Any]:
    return {"text_run": {"content": content, "text_element_style": {}}}


def _text_block(text: str) -> dict[str, Any]:
    return {"block_type": 2, "text": {"elements": [_text_run(text)], "style": {}}}


def _heading_block(level: int, text: str) -> dict[str, Any]:
    # block_type: text=2, heading1=3 ... heading9=11
    return {"block_type": 2 + level, f"heading{level}": {"elements": [_text_run(text)], "style": {}}}


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {"code": -1, "msg": f"非 JSON 响应 {response.status_code}"}
    return data if isinstance(data, dict) else {"code": -1, "msg": "响应格式异常"}


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}
