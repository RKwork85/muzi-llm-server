"""小红书登录态体检：先做零应用层流量的连通性检查，再做「单次」只读页面探测。

阶段 A（默认）：DNS / TCP / TLS 三个域名的连通性——不发送任何 HTTP 请求、不带 Cookie。
阶段 B（--probe）：向 https://www.xiaohongshu.com/explore 发送 **一次** GET，
                   带本地 Cookie 头 + 桌面 Chrome UA，检查是否被识别为已登录。

刻意不做的事：不打开浏览器、不走扫码登录、不重试、不做第二个业务请求，
以免在「只验证一次」的约束下反复触发小红书风控。
"""

import argparse
import json
import re
import socket
import ssl
import time

AUTH = r"C:\Users\rkwork\Documents\Qs-data\auth\xhs_cookie.txt"
OUT_HTML = "_probe_xhs.html"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def phase_a(hosts: list[str]) -> None:
    print("=" * 70)
    print("阶段 A：连通性（DNS / TCP / TLS，无 HTTP 请求、无 Cookie）")
    print("=" * 70)
    for h in hosts:
        t0 = time.time()
        try:
            ip = socket.getaddrinfo(h, 443, proto=socket.IPPROTO_TCP)[0][4][0]
            print(f"{h:30s} DNS  ok   {ip:16s} {(time.time()-t0)*1000:6.0f} ms")
        except Exception as exc:  # noqa: BLE001
            print(f"{h:30s} DNS  FAIL {type(exc).__name__}: {exc}")
            continue
        t0 = time.time()
        try:
            sock = socket.create_connection((h, 443), timeout=8)
            ctx = ssl.create_default_context()
            tls = ctx.wrap_socket(sock, server_hostname=h)
            subj = dict(x[0] for x in tls.getpeercert().get("subject", ()))
            print(
                f"{h:30s} TLS  ok   {tls.version():10s} "
                f"{(time.time()-t0)*1000:6.0f} ms  CN={subj.get('commonName', '?')}"
            )
            tls.close()
        except Exception as exc:  # noqa: BLE001
            print(f"{h:30s} TLS  FAIL {type(exc).__name__}: {exc}")


def cookie_header() -> str:
    return open(AUTH, encoding="utf-8").read().strip()


def phase_b(url: str) -> int:
    import httpx

    print()
    print("=" * 70)
    print("阶段 B：单次只读探测（1 次 GET，带本地 Cookie）")
    print("=" * 70)
    print("URL      :", url)
    ck = cookie_header()
    jar_keys = [p.split("=", 1)[0].strip() for p in ck.split(";") if "=" in p]
    print("Cookie   :", len(jar_keys), "个键", jar_keys)

    headers = {
        "User-Agent": UA,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Cookie": ck,
        "Upgrade-Insecure-Requests": "1",
    }
    t0 = time.time()
    with httpx.Client(timeout=25, follow_redirects=True, trust_env=False) as c:
        resp = c.get(url, headers=headers)
    cost = (time.time() - t0) * 1000

    html = resp.text
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"状态码   : {resp.status_code}  耗时 {cost:.0f} ms  大小 {len(html)} 字符")
    print("最终 URL :", resp.url)
    print("重定向   :", [str(h.headers.get("location")) for h in resp.history] or "无")

    title = re.search(r"<title>(.*?)</title>", html, re.S)
    print("页面标题 :", (title.group(1).strip()[:80] if title else "无 <title>"))

    flags = {
        '"loggedIn":true': "INITIAL_STATE loggedIn=true",
        '"loggedIn":false":': None,  # 占位，避免误匹配
        '"loggedIn":false': "INITIAL_STATE loggedIn=false",
        "login-container": "页面含登录弹窗节点",
        "扫码登录": "页面含「扫码登录」字样",
        "安全验证": "页面含「安全验证」（风控）",
        "verify": "页面含 verify（风控/验证）",
        "__INITIAL_STATE__": "页面含 __INITIAL_STATE__",
        '"userPageData"': '含 userPageData（用户信息）',
        '"redId"': "含 redId（账号信息）",
        '"nickname"': "含 nickname（账号信息）",
    }
    print()
    print("信号命中 :")
    for needle, label in flags.items():
        if not label:
            continue
        print(f"   {'✅' if needle in html else '❌'} {label}")

    # 提取昵称，作为「确实登录成谁」的实证（不打印 cookie 值）
    nick = None
    for pat in (r'"nickname":"([^"]{1,40})"', r'"nickName":"([^"]{1,40})"'):
        m = re.search(pat, html)
        if m:
            nick = m.group(1)
            break
    print("识别昵称 :", nick or "（页面未内联账号信息）")
    print("Set-Cookie:", [k for k in resp.headers.get_list("set-cookie")][:3] or "无")

    verdict = "未知"
    if '"loggedIn":true' in html and '"loggedIn":false' not in html:
        verdict = "Cookie 有效（服务端返回已登录状态）"
    elif '"loggedIn":false' in html:
        verdict = "Cookie 已失效（服务端返回未登录）"
    elif resp.status_code in (403, 412, 461) or "安全验证" in html:
        verdict = "被风控拦截，Cookie 有效性无法判定"
    print()
    print("判定     :", verdict)

    summary = {
        "status": resp.status_code,
        "final_url": str(resp.url),
        "title": title.group(1).strip() if title else "",
        "nickname": nick,
        "logged_in_true": '"loggedIn":true' in html,
        "logged_in_false": '"loggedIn":false' in html,
        "risk_block": bool(resp.status_code in (403, 412, 461) or "安全验证" in html),
        "verdict": verdict,
        "elapsed_ms": round(cost),
    }
    with open("_probe_result.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print()
    print("原始 HTML 已存 ->", OUT_HTML)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="执行单次只读 HTTP 探测")
    ap.add_argument(
        "--url", default="https://www.xiaohongshu.com/explore", help="探测的 URL"
    )
    args = ap.parse_args()
    phase_a(["www.xiaohongshu.com", "edith.xiaohongshu.com", "sns-webpic-qc.xhscdn.com"])
    if args.probe:
        raise SystemExit(phase_b(args.url))
