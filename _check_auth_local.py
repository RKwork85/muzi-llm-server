"""本地登录态体检（零网络、零小红书访问）。

只看三件事：
1. Cookie 文件本身：键是否齐、什么时候被写过、值是否像样。
2. 采集服务（控制服）留下的任务记录：有没有跑过 xhs.login、结果如何。
3. 最近一次真实采集是否用这份 Cookie 成功（用 content_tasks + 批次目录旁证）。

不做任何 xiaohongshu.com 请求，避免违反「只验证一次」的约束。
"""

import json
import os
import re
import datetime as dt

AUTH = r"C:\Users\rkwork\Documents\Qs-data\auth\xhs_cookie.txt"
TASKS = r"C:\Users\rkwork\Documents\Qs-data\control-server\tasks.json"
DATA_XHS = r"C:\Users\rkwork\Documents\Qs-data\data\xhs"


def mask(v: str) -> str:
    if len(v) <= 10:
        return v[:2] + "*" * max(0, len(v) - 2)
    return f"{v[:6]}...{v[-4:]}(len={len(v)})"


def parse_cookie(raw: str) -> dict:
    out = {}
    for part in raw.split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def main() -> None:
    raw = open(AUTH, encoding="utf-8").read().strip()
    jar = parse_cookie(raw)

    print("=" * 64)
    print("1) Cookie 文件体检")
    print("=" * 64)
    print(f"路径      : {AUTH}")
    print(f"大小      : {len(raw)} bytes / {raw.count(';') + 1} 个键")
    print(f"写入时间  : {dt.datetime.fromtimestamp(os.path.getmtime(AUTH))}")
    print(f"创建时间  : {dt.datetime.fromtimestamp(os.path.getctime(AUTH))}")
    required = ["a1", "web_session", "webId"]
    print(f"必需键    : {required}")
    print(f"缺失      : {[k for k in required if k not in jar] or '无'}")
    print(f"会话键    : web_session={'有' if jar.get('web_session') else '无'} "
          f"| id_token={'有' if jar.get('id_token') else '无'} "
          f"| gid={'有' if jar.get('gid') else '无'}")
    for k in ("ets", "loadts"):
        if jar.get(k, "").isdigit():
            print(f"{k:10s}: {jar[k]} -> "
                  f"{dt.datetime.fromtimestamp(int(jar[k]) / 1000)}")
    print("字段一览  :")
    for k, v in jar.items():
        print(f"    {k:22s} {mask(v)}")

    print()
    print("=" * 64)
    print("2) 采集服务任务记录（xhs.login / 最近采集）")
    print("=" * 64)
    if not os.path.exists(TASKS):
        print("tasks.json 不存在")
    else:
        data = json.load(open(TASKS, encoding="utf-8"))
        tasks = data.get("tasks", [])
        print(f"总任务数: {len(tasks)}")
        for t in tasks[:10]:
            print(f"  {t.get('createdAt')}  {str(t.get('type')):20s} "
                  f"{str(t.get('status')):8s} "
                  f"err={str(t.get('error'))[:50]}")
        logins = [t for t in tasks if "login" in str(t.get("type"))]
        print(f"登录任务: {len(logins)}")
        for t in logins[:5]:
            print(f"  {t.get('createdAt')} {t.get('type')} {t.get('status')} "
                  f"result={json.dumps(t.get('result'), ensure_ascii=False)[:160]}")

    print()
    print("=" * 64)
    print("3) 最近批次（Cookie 可用性的旁证）")
    print("=" * 64)
    if os.path.isdir(DATA_XHS):
        rows = []
        for name in os.listdir(DATA_XHS):
            full = os.path.join(DATA_XHS, name)
            if os.path.isdir(full):
                rows.append((os.path.getmtime(full), name))
        rows.sort(reverse=True)
        for mtime, name in rows[:5]:
            files = []
            for root, _dirs, fs in os.walk(os.path.join(DATA_XHS, name)):
                files.extend(fs)
            print(f"  {dt.datetime.fromtimestamp(mtime)}  {name}  文件 {len(files)} 个")
    else:
        print("data/xhs 目录不存在")

    print()
    print("a1 结构 :", len(jar.get("a1", "")), "字符, 纯 hex:",
          bool(re.fullmatch(r"[0-9a-f]+", jar.get("a1", ""))))


if __name__ == "__main__":
    main()
