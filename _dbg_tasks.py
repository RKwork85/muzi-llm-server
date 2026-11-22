import asyncio, httpx, json
async def main():
    async with httpx.AsyncClient(timeout=30, trust_env=False) as c:
        r = await c.post("http://127.0.0.1:8001/api/v1/auth/login", json={"email":"muzi@gmail.com","password":"muzimuzi"})
        print("login", r.status_code)
        tok = r.json()["token"]
        h={"Authorization":f"Bearer {tok}"}
        r = await c.get("http://127.0.0.1:8001/api/v1/content/tasks?limit=20", headers=h)
        data=r.json()
        print("total", data.get("total"))
        for t in data.get("items", []):
            pv = t.get("preview_data")
            notes = (pv or {}).get("notes") if isinstance(pv, dict) else None
            print(t["id"][:8], t["status"], t["stage"], "err=", (t.get("error") or "")[:120], "notes=", len(notes or []), "created=", t.get("created_at"))
asyncio.run(main())
