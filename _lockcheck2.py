import asyncio, asyncpg

async def main():
    conn = await asyncpg.connect(host="127.0.0.1", port=5432, user="muzi_admin",
                                password="LNO6oZbWvZNSYABvSVuq453eECoOVzce", database="muzi_llm")
    rows = await conn.fetch("""
        select pid, application_name, client_addr, client_port, state,
               round(extract(epoch from (now()-xact_start)))::int as xact_age,
               backend_start
        from pg_stat_activity
        where datname='muzi_llm' and state='idle in transaction'
    """)
    for r in rows:
        print(dict(r))
    await conn.close()

asyncio.run(main())
