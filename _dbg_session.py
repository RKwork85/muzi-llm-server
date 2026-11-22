import asyncio, httpx, json
async def main():
    async with httpx.AsyncClient(timeout=20, trust_env=False) as c:
        r = await c.post("http://127.0.0.1:8001/api/v1/auth/login", json={"email":"muzi@gmail.com","password":"muzimuzi"})
        b=r.json()
        payload={"token":b["token"],"tokenType":"bearer","expiresAt":b.get("expires_at") or "","user":b.get("user")}
        print(json.dumps(payload, ensure_ascii=False))
asyncio.run(main())
