"""企业微信 OpenAPI 桥接。凭据按用户隔离，access_token 按用户进程内缓存。"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

BASE = "https://qyapi.weixin.qq.com/cgi-bin"
_TOKEN_TTL = 7000  # 官方 7200 秒，提前 200 秒刷新

_lock = asyncio.Lock()
# user_id -> {"token": str, "expires": float}
_cached: dict[str, dict[str, Any]] = {}


def configured(credentials: dict[str, Any]) -> bool:
    return bool(
        str(credentials.get("corp_id", "")).strip()
        and str(credentials.get("secret", "")).strip()
        and str(credentials.get("agent_id", "")).strip()
    )


def tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "send_text",
            "description": "向指定企业微信成员发送应用文本消息。touser 为成员 UserID，多个用竖线 | 分隔。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "touser": {"type": "string", "description": "接收消息的成员 UserID，多个用 | 分隔"},
                    "content": {"type": "string", "description": "消息文本内容，最长约 2000 字节"},
                },
                "required": ["touser", "content"],
            },
        },
        {
            "name": "list_departments",
            "description": "列出企业微信通讯录的部门列表。",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


async def _access_token(user_id: str, credentials: dict[str, Any], timeout: float) -> str:
    now = time.monotonic()
    cached = _cached.get(user_id)
    if cached and cached.get("token") and cached["expires"] > now:
        return cached["token"]
    async with _lock:
        now = time.monotonic()
        cached = _cached.get(user_id)
        if cached and cached.get("token") and cached["expires"] > now:
            return cached["token"]
        params = {"corpid": credentials["corp_id"], "corpsecret": credentials["secret"]}
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{BASE}/gettoken", params=params)
        body = _json(response)
        if body.get("errcode") != 0:
            raise RuntimeError(f"wecom gettoken failed: {body.get('errmsg')}")
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("wecom gettoken missing token")
        _cached[user_id] = {"token": token, "expires": now + _TOKEN_TTL}
        return token


async def call_tool(user_id: str, credentials: dict[str, Any], timeout: float, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        if name == "send_text":
            text = await _send_text(user_id, credentials, timeout, arguments)
        elif name == "list_departments":
            text = await _list_departments(user_id, credentials, timeout)
        else:
            return _error(f"未知工具 {name}")
        return _ok(text)
    except Exception as exc:  # 远端调用失败统一折叠，不把堆栈暴露给模型
        return _error(str(exc))


async def _send_text(user_id: str, credentials: dict[str, Any], timeout: float, arguments: dict[str, Any]) -> str:
    touser = str(arguments.get("touser", "")).strip()
    content = str(arguments.get("content", "")).strip()
    if not touser or not content:
        raise ValueError("touser 和 content 不能为空")
    token = await _access_token(user_id, credentials, timeout)
    payload = {
        "touser": touser,
        "msgtype": "text",
        "agentid": int(credentials["agent_id"]),
        "text": {"content": content},
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{BASE}/message/send", params={"access_token": token}, json=payload)
    body = _json(response)
    if body.get("errcode") != 0:
        raise RuntimeError(f"wecom send failed: {body.get('errmsg')}")
    return json.dumps({"sent": True, "msgid": body.get("msgid")}, ensure_ascii=False)


async def _list_departments(user_id: str, credentials: dict[str, Any], timeout: float) -> str:
    token = await _access_token(user_id, credentials, timeout)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(f"{BASE}/department/list", params={"access_token": token})
    body = _json(response)
    if body.get("errcode") != 0:
        raise RuntimeError(f"wecom department/list failed: {body.get('errmsg')}")
    departments = body.get("department")
    if not isinstance(departments, list):
        departments = []
    return json.dumps({"departments": departments}, ensure_ascii=False)


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {"errcode": -1, "errmsg": f"非 JSON 响应 {response.status_code}"}
    return data if isinstance(data, dict) else {"errcode": -1, "errmsg": "响应格式异常"}


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}
