import asyncio
import httpx
import json

URL = "https://www.xiaohongshu.com/explore/6b005759000000001f016ee5?xsec_token=AB_LhOCAxV7YvOQ0aphz7BxjPSOr_6uE5UAMnkqi4hCrw=&xsec_source=pc_feed"


def summarize(o, depth=0, maxd=4):
    if depth > maxd:
        return "..."
    if isinstance(o, dict):
        return {k: summarize(v, depth + 1, maxd) for k, v in list(o.items())[:10]}
    if isinstance(o, list):
        return [summarize(x, depth + 1, maxd) for x in o[:4]]
    if isinstance(o, str):
        return o[:80]
    return o


async def one(c, tok, n):
    r = await c.post(
        "http://127.0.0.1:3780/api/collect/run",
        json={"type": "xhs.crawl_batch", "params": {"urls": [URL], "user_id": "803445db-1e06-4be6-ba50-99f2e842cf50", "task_id": f"t{n}"}},
        headers={"Authorization": f"Bearer {tok}"},
    )
    print(f"\n===== #{n} HTTP {r.status_code} =====")
    body = r.json()
    print("top keys:", list(body.keys()))
    print("ok:", body.get("ok"))
    if body.get("ok") is not True:
        print("error:", body.get("error"))
        return
    result = body.get("result")
    print("result type:", type(result).__name__)
    if isinstance(result, dict):
        print("result keys:", list(result.keys()))
        results = result.get("results")
        if isinstance(results, list):
            for i, item in enumerate(results):
                print(f"  results[{i}] keys:", list(item.keys()) if isinstance(item, dict) else type(item).__name__)
                if isinstance(item, dict):
                    r0 = item.get("result")
                    print(f"    result type:", type(r0).__name__)
                    if isinstance(r0, dict):
                        print(f"    result keys:", list(r0.keys()))
                        pv = r0.get("preview")
                        print(f"    preview type:", type(pv).__name__)
                        if isinstance(pv, dict):
                            print(f"    preview keys:", list(pv.keys()))
                            notes = pv.get("notes")
                            if isinstance(notes, list) and notes:
                                n0 = notes[0]
                                print(f"    note0 video_url={'Y' if n0.get('video_url') else 'N'} images={len(n0.get('images') or [])} cover={'Y' if n0.get('cover_url') else 'N'}")
                    else:
                        print(f"    result(raw):", json.dumps(r0, ensure_ascii=False)[:200] if not isinstance(r0, str) else r0[:200])
    else:
        print("result(raw):", json.dumps(result, ensure_ascii=False)[:300])


async def main():
    async with httpx.AsyncClient(timeout=180, trust_env=False) as c:
        r = await c.post(
            "http://127.0.0.1:3780/api/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        tok = r.json()["token"]
        for i in range(1, 7):
            await one(c, tok, i)


asyncio.run(main())
