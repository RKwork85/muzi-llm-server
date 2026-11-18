import asyncio, asyncpg

async def main():
    conn = await asyncpg.connect(host="127.0.0.1", port=5432, user="muzi_admin",
                                password="LNO6oZbWvZNSYABvSVuq453eECoOVzce", database="muzi_llm")
    rows = await conn.fetch("""
        select pid, state, wait_event_type, wait_event,
               round(extract(epoch from (now()-xact_start)))::int as xact_age,
               left(regexp_replace(query, '\\s+', ' ', 'g'), 80) as q
        from pg_stat_activity where datname='muzi_llm' order by xact_start nulls last
    """)
    for r in rows:
        print(dict(r))
    await conn.close()

asyncio.run(main())
