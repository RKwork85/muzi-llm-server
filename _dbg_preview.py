import asyncio, httpx, json
async def main():
    async with httpx.AsyncClient(timeout=30, trust_env=False) as c:
        r = await c.post("http://127.0.0.1:8001/api/v1/auth/login", json={"email":"muzi@gmail.com","password":"muzimuzi"})
        tok = r.json()["token"]; h={"Authorization":f"Bearer {tok}"}
        r = await c.get("http://127.0.0.1:8001/api/v1/content/tasks?limit=20", headers=h)
        for t in r.json()["items"]:
            if t["status"]=="draft" and t.get("preview_data"):
                print(json.dumps(t["preview_data"], ensure_ascii=False, indent=1)[:2600])
                return
asyncio.run(main())
