import asyncio, asyncpg

async def main():
    conn = await asyncpg.connect(host="127.0.0.1", port=5432, user="muzi_admin",
                                password="LNO6oZbWvZNSYABvSVuq453eECoOVzce", database="muzi_llm")
    row = await conn.fetchrow("select id, username from users order by created_at limit 1")
    print("sample user:", row["username"])
    uid = row["id"]

    async def attempt(value, expect_ok):
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute("update users set username=$1 where id=$2", value, uid)
            print(f"{value!r:>20} -> OK", "✔" if expect_ok else "✘(应为非法)")
        except asyncpg.exceptions.CheckViolationError as e:
            print(f"{value!r:>20} -> 被约束拒绝", "✔" if not expect_ok else "✘(应为合法)")
        finally:
            await tr.rollback()

    await attempt("小明", True)
    await attempt("木子_01", True)
    await attempt("MUZI", False)      # 必须是小写归一
    await attempt("有 空格", False)
    await attempt("a", False)          # 少于 2 字符
    await conn.close()

asyncio.run(main())
