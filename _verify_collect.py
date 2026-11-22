"""一次性真实采集验证（不重试、不重复提交）。

链路：登录态 → POST /api/v1/content/collect（真实入队）→ 轮询任务
     → 校验 preview_data → 用本地 OSS 网关复核媒体可读 → 输出模板跳转链接。
只提交一次采集任务，避免多次访问小红书。
"""

import asyncio
import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8001"
OSS = "http://127.0.0.1:8891"
OSS_TOKEN = "pBhKcQ6ryduPXziS7DJgfIZmt4UHaoA0v3q5FRnY"
URL = (
    "https://www.xiaohongshu.com/explore/6afc2b94000000002a00f38a"
    "?xsec_token=AB0BnD-isFb6c9kdGGCKa3d_4mH0Aa0hkLrJhUxh__W0k=&xsec_source=pc_feed"
)
SHOW = "https://muzihhh.com/muzi_xhs_note_showpage?task_id="


def log(*parts):
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


async def main() -> int:
    async with httpx.AsyncClient(timeout=60, trust_env=False) as c:
        r = await c.post(
            f"{BASE}/api/v1/auth/login",
            json={"email": "muzi@gmail.com", "password": "muzimuzi"},
        )
        r.raise_for_status()
        tok = r.json()["token"]
        headers = {"Authorization": f"Bearer {tok}"}
        log("login ok")

        # —— 唯一一次采集提交 ——
        r = await c.post(
            f"{BASE}/api/v1/content/collect",
            json={"links": [{"platform": "xhs", "url": URL}]},
            headers=headers,
        )
        log("collect POST", r.status_code, r.text[:300])
        if r.status_code != 200:
            return 1
        task_id = r.json()["task_id"]
        log("task_id =", task_id)

        preview = None
        task = None
        deadline = time.time() + 240
        while time.time() < deadline:
            await asyncio.sleep(2)
            rr = await c.get(f"{BASE}/api/v1/content/tasks/{task_id}", headers=headers)
            if rr.status_code != 200:
                log("query", rr.status_code, rr.text[:200])
                continue
            task = rr.json()
            log("  status=", task["status"], "error=", (task.get("error") or "")[:160])
            if task["status"] in ("draft", "failed", "cancelled", "published", "succeeded"):
                preview = task.get("preview_data")
                break

        print("\n===== RESULT =====")
        print("status:", task and task["status"])
        print("error :", (task or {}).get("error"))
        if not isinstance(preview, dict) or not preview.get("notes"):
            print("NO PREVIEW")
            return 2

        note = preview["notes"][0]
        print("note_id    :", note.get("note_id"))
        print("type       :", note.get("type"))
        print("title      :", note.get("title"))
        print("publish    :", note.get("publish_time"), "| ip:", note.get("ip_location"))
        print("author     :", (note.get("author") or {}).get("nickname"),
              "| avatar:", bool((note.get("author") or {}).get("avatar_url")))
        print("topics     :", note.get("topics"))
        print("stats      :", note.get("stats"))
        print("images     :", len(note.get("images") or []))
        print("video_url  :", "Y" if note.get("video_url") else "N")
        print("cover_url  :", "Y" if note.get("cover_url") else "N")
        print("desc_len   :", len(note.get("desc") or ""))
        print("oss_keys   :", len(note.get("oss_keys") or []))

        media = [u for u in (note.get("images") or []) if u]
        if note.get("video_url"):
            media.append(note["video_url"])
        avatar = (note.get("author") or {}).get("avatar_url")
        if avatar:
            media.append(avatar)
        bad = []
        for u in media:
            key = u.split("/files/", 1)[-1]
            rr = await c.head(
                f"{OSS}/files/{key}", headers={"X-Internal-Token": OSS_TOKEN}
            )
            if rr.status_code != 200:
                bad.append((rr.status_code, key))
        print(f"media check: {len(media) - len(bad)}/{len(media)} ok")
        for code, key in bad:
            print("  BAD", code, key)

        print("\nSHOWPAGE:", SHOW + task_id)
        with open("_verify_result.json", "w", encoding="utf-8") as f:
            json.dump({"task_id": task_id, "status": task["status"],
                       "error": task.get("error"), "preview": preview,
                       "media_bad": bad}, f, ensure_ascii=False, indent=2)
        return 0 if not bad else 3


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
