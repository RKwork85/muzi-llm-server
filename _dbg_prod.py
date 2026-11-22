import asyncio, httpx
async def main():
    async with httpx.AsyncClient(timeout=20, trust_env=False) as c:
        r = await c.post("http://127.0.0.1:8001/api/v1/auth/login", json={"email":"muzi@gmail.com","password":"muzimuzi"})
        tok = r.json()["token"]
        print("local login", r.status_code, "user_id=", r.json().get("user",{}).get("id") or r.json().get("user_id"))
        for base in ["https://muzihhh.com", "http://127.0.0.1:8001"]:
            try:
                r2 = await c.get(base+"/api/v1/content/tasks?limit=1", headers={"Authorization":f"Bearer {tok}"})
                print(base, r2.status_code, (r2.text or "")[:160])
            except Exception as e:
                print(base, "ERR", type(e).__name__, str(e)[:120])
asyncio.run(main())
