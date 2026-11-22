import asyncio, httpx
async def main():
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("http://127.0.0.1:8001/api/v1/auth/login", json={"email":"muzi@gmail.com","password":"muzimuzi"})
        tok = r.json()["token"]
        try:
            r2 = await c.get("https://muzihhh.com/api/v1/content/tasks?limit=1", headers={"Authorization":f"Bearer {tok}"})
            print("prod api", r2.status_code, (r2.text or "")[:200])
        except Exception as e:
            print("prod api ERR", type(e).__name__, str(e)[:200])
        try:
            r3 = await c.get("https://muzihhh.com/muzi_xhs_note_showpage")
            print("showpage", r3.status_code, len(r3.text), r3.headers.get("content-type"))
        except Exception as e:
            print("showpage ERR", type(e).__name__, str(e)[:200])
asyncio.run(main())
