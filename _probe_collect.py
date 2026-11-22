import asyncio
import httpx

URL = "https://www.xiaohongshu.com/explore/6b005759000000001f016ee5?xsec_token=AB_LhOCAxV7YvOQ0aphz7BxjPSOr_6uE5UAMnkqi4hCrw=&xsec_source=pc_feed"

async def one(c, tok, n):
    r2 = await c.post(
        "http://127.0.0.1:8001/api/v1/content/collect",
        json={"links": [{"platform": "xhs", "url": URL}]},
        headers={"Authorization": f"Bearer {tok}"},
    )
    tid = r2.json()["task_id"]
    for _ in range(40):
        await asyncio.sleep(3)
        r3 = await c.get(
            f"http://127.0.0.1:8001/api/v1/content/tasks/{tid}",
            headers={"Authorization": f"Bearer {tok}"},
        )
        t = r3.json()
        if t["status"] in ("draft", "failed", "succeeded", "published"):
            pv = t.get("preview_data")
            media = ""
            if isinstance(pv, dict) and pv.get("notes"):
                note = pv["notes"][0]
                media = (
                    "video=" + ("Y" if note.get("video_url") else "N")
                    + " img=" + str(len(note.get("images") or []))
                    + " cover=" + ("Y" if note.get("cover_url") else "N")
                )
            print(f"#{n} {tid[:8]} -> {t['status']} err={t['error']} {media}")
            return
    print(f"#{n} {tid[:8]} -> timeout")


async def main():
    async with httpx.AsyncClient(timeout=120, trust_env=False) as c:
        r = await c.post(
            "http://127.0.0.1:8001/api/v1/auth/login",
            json={"email": "muzi@gmail.com", "password": "muzimuzi"},
        )
        tok = r.json()["token"]
        for i in range(1, 5):
            await one(c, tok, i)


asyncio.run(main())
