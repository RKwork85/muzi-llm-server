"""进程内假 MCP 服务。用换行 JSON 或 Content-Length 帧说话，不访问网络。"""

from __future__ import annotations

import json
import os
import sys
import time

_REVERSE = (
    (7, "sampling/createMessage"),
    (8, "resources/read"),
    (9, "prompts/get"),
    (10, "elicitation/create"),
    (11, "roots/list"),
)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    capture = sys.argv[2] if len(sys.argv) > 2 else ""
    if mode == "crash":
        return
    framed = mode == "framed"
    step = 0
    want_list = False

    def write(payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if framed:
            sys.stdout.buffer.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii") + data)
        else:
            sys.stdout.buffer.write(data + b"\n")
        sys.stdout.buffer.flush()

    def read() -> dict | None:
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                return None
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                message = json.loads(text)
                if isinstance(message, dict):
                    return message

    def send_list() -> None:
        write(
            {
                "jsonrpc": "2.0",
                "id": pending_list_id,
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "第一行\n第二行\x00" + ("长" * 300),
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "message": {"type": "string", "description": "远端说明"},
                                    "bad prop": {"type": "string"},
                                    "1bad": {"type": "number"},
                                },
                                "required": ["message", "missing"],
                            },
                        },
                        {"name": "secret", "description": "不要导入"},
                        {"name": "bad name", "description": "名字不合法"},
                    ]
                },
            }
        )

    def send_reverse() -> None:
        ident, method = _REVERSE[step]
        write({"jsonrpc": "2.0", "id": ident, "method": method, "params": {}})

    pending_list_id = None
    list_count = 0
    while True:
        message = read()
        if message is None:
            return
        if mode == "badjson":
            sys.stdout.buffer.write(b"{not-json\n")
            sys.stdout.buffer.flush()
            return
        method = message.get("method")
        message_id = message.get("id")
        if method is None and message_id in {item[0] for item in _REVERSE}:
            if message.get("error", {}).get("code") != -32601:
                raise SystemExit(2)
            step += 1
            if step < len(_REVERSE):
                send_reverse()
            elif want_list and pending_list_id is not None:
                send_list()
            continue
        if method == "initialize":
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            requested = params.get("protocolVersion") if isinstance(params.get("protocolVersion"), str) else ""
            version = "2024-11-05"
            if mode == "bad-version":
                version = "2026-07-28"
            elif mode == "prefer" and requested:
                version = requested
            write(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {
                        "protocolVersion": version,
                        "capabilities": {"tools": {"listChanged": True}} if mode == "changed" else {},
                        "serverInfo": {"name": "fake", "version": "1"},
                    },
                }
            )
            if mode == "reverse":
                send_reverse()
            continue
        if method == "notifications/initialized" or method == "notifications/cancelled":
            continue
        if method == "tools/list":
            pending_list_id = message_id
            if mode == "reverse" and step < len(_REVERSE):
                want_list = True
            elif mode == "ping":
                write({"jsonrpc": "2.0", "id": 99, "method": "ping"})
                reply = read()
                if not isinstance(reply, dict) or reply.get("id") != 99 or reply.get("result") != {}:
                    raise SystemExit(3)
                send_list()
            elif mode == "pages":
                params = message.get("params") if isinstance(message.get("params"), dict) else {}
                cursor = params.get("cursor")
                if cursor == "p2":
                    write(
                        {
                            "jsonrpc": "2.0",
                            "id": message_id,
                            "result": {"tools": [{"name": "echo", "description": "第二页", "inputSchema": {"type": "object"}}]},
                        }
                    )
                else:
                    write(
                        {
                            "jsonrpc": "2.0",
                            "id": message_id,
                            "result": {
                                "tools": [{"name": "marker", "description": "第一页", "inputSchema": {"type": "object"}}],
                                "nextCursor": "p2",
                            },
                        }
                    )
            elif mode == "changed":
                list_count += 1
                if list_count == 1:
                    send_list()
                    write({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
                else:
                    write(
                        {
                            "jsonrpc": "2.0",
                            "id": message_id,
                            "result": {"tools": [{"name": "echo", "description": "更新后", "inputSchema": {"type": "object"}}]},
                        }
                    )
            else:
                send_list()
            continue
        if method == "tools/call":
            if mode == "cancel":
                nxt = read()
                if capture and isinstance(nxt, dict):
                    with open(capture, "w", encoding="utf-8") as handle:
                        json.dump(nxt, handle)
                return
            if mode == "hang":
                time.sleep(30)
                return
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            text = arguments.get("message", "")
            if text == "bad":
                write(
                    {
                        "jsonrpc": "2.0",
                        "id": message_id,
                        "error": {"code": -32602, "message": "Unknown tool: invalid_tool_name"},
                    }
                )
                continue
            if text == "__env__":
                text = "dirty" if "DEEPSEEK_API_KEY" in os.environ else "clean"
            write(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {"content": [{"type": "text", "text": str(text)}], "isError": False},
                }
            )
            continue
        if message_id is not None:
            write(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {"code": -32601, "message": "Method not found"},
                }
            )


if __name__ == "__main__":
    main()
