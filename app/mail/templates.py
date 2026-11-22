"""验证码邮件正文。同时提供 text/plain 与 text/html：只有 HTML 的邮件更容易被判垃圾邮件。"""

from __future__ import annotations

from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from app.core.config import Settings

_SCENE_PURPOSES = {
    "register": "注册验证码",
    "login": "注册验证码",
    "bind": "绑定邮箱验证码",
    "reset_password": "重置密码验证码",
}
# 正文里「完成 X」的动作词。用词跟标题区分开，避免整封信反复念同一句。
_SCENE_ACTIONS = {
    "register": "注册登录",
    "login": "注册登录",
    "bind": "绑定邮箱",
    "reset_password": "重置密码",
}


def scene_purpose(scene: str) -> str:
    return _SCENE_PURPOSES.get(scene, "注册验证码")


def scene_action(scene: str) -> str:
    return _SCENE_ACTIONS.get(scene, "注册登录")


def _subject(settings: Settings, purpose: str) -> str:
    """标题按用途区分。

    MAIL_SUBJECT 只用来覆盖「注册验证码」这一种默认标题；重置密码等场景必须
    自带自己的用途词，否则用户收到的重置邮件会写着「注册验证码」。
    """
    configured = (getattr(settings, "mail_subject", "") or "").strip()
    if configured and purpose == "注册验证码":
        return configured
    brand = (getattr(settings, "mail_from_name", "") or "").strip()
    return f"{brand} {purpose}".strip()


def build_code_message(
    settings: Settings,
    to_email: str,
    code: str,
    *,
    scene: str,
    from_addr: str | None = None,
    from_name: str | None = None,
) -> EmailMessage:
    """构造验证码邮件。

    from_addr / from_name 用于故障切换：备用账号必须用自己认证过的发信地址，
    否则 QQ 这类邮箱会以伪造发件人为由拒收。
    """
    purpose = scene_purpose(scene)
    action = scene_action(scene)
    minutes = max(1, round(settings.mail_code_ttl_seconds / 60))
    address = from_addr or settings.mail_from
    sender = formataddr((from_name if from_name is not None else (settings.mail_from_name or ""), address))
    # Message-ID 用发信域：与 From 同域能少挨一点垃圾邮件打分。
    domain = address.rsplit("@", 1)[-1] if "@" in address else (settings.mail_host or "smtp")
    subject = _subject(settings, purpose)

    message = EmailMessage()
    message["From"] = sender
    message["To"] = to_email
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=domain)
    # 同一封信可能被投递多次（重试/切换账号），标注自动生成且无需回复。
    message["Auto-Submitted"] = "auto-generated"

    # 正文不再重复品牌名：标题里已经带了，列表预览再重复一次会显得啰嗦。
    text = (
        f"验证码：{code}\n\n"
        f"请在页面中输入下面的验证码完成{action}，{minutes} 分钟内有效，仅可使用一次。\n"
        f"如果这不是你本人的操作，请忽略本邮件，你的账户不会受到影响。\n"
    )
    html = f"""\
<!doctype html>
<html lang="zh-CN">
  <body style="margin:0;padding:24px;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
    <div style="max-width:480px;margin:0 auto;padding:32px;background:#ffffff;border-radius:12px;">
      <h1 style="margin:0 0 16px;color:#171717;font-size:20px;font-weight:600;">{purpose}</h1>
      <p style="margin:0 0 20px;color:#404040;font-size:14px;line-height:1.6;">请在页面中输入下面的验证码完成{action}：</p>
      <p style="margin:0 0 20px;padding:16px;background:#f5f5f5;border-radius:10px;color:#171717;font-size:30px;font-weight:700;letter-spacing:8px;text-align:center;">{code}</p>
      <p style="margin:0 0 8px;color:#737373;font-size:13px;line-height:1.6;">验证码 {minutes} 分钟内有效，仅可使用一次。</p>
      <p style="margin:0;color:#a3a3a3;font-size:12px;line-height:1.6;">如果这不是你本人的操作，请忽略本邮件，你的账户不会受到影响。</p>
    </div>
  </body>
</html>
"""
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    return message
