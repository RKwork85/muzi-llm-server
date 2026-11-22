"""验证码邮件正文。同时提供 text/plain 与 text/html：只有 HTML 的邮件更容易被判垃圾邮件。"""

from __future__ import annotations

from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from app.core.config import Settings

_SCENE_LABELS = {
    "login": "登录",
    "bind": "绑定邮箱",
    "reset_password": "重置密码",
}


def scene_label(scene: str) -> str:
    return _SCENE_LABELS.get(scene, "登录")


def build_code_message(settings: Settings, to_email: str, code: str, *, scene: str) -> EmailMessage:
    """构造验证码邮件。发件人必须等于已认证账号，QQ 邮箱会拒绝伪造 From。"""
    label = scene_label(scene)
    minutes = max(1, round(settings.mail_code_ttl_seconds / 60))
    host = settings.mail_host or "smtp"
    sender = formataddr((settings.mail_from_name or "", settings.mail_from))

    message = EmailMessage()
    message["From"] = sender
    message["To"] = to_email
    message["Subject"] = settings.mail_subject or f"MUZI WORLD {label}验证码"
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=host)

    text = (
        f"你的 MUZI WORLD {label}验证码是：{code}\n\n"
        f"验证码 {minutes} 分钟内有效，仅可使用一次。\n"
        f"如果这不是你本人的操作，请忽略本邮件，你的账户不会受到影响。\n"
    )
    html = f"""\
<!doctype html>
<html lang="zh-CN">
  <body style="margin:0;padding:24px;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
    <div style="max-width:480px;margin:0 auto;padding:32px;background:#ffffff;border-radius:12px;">
      <p style="margin:0 0 8px;color:#737373;font-size:13px;letter-spacing:0.04em;">MUZI WORLD</p>
      <h1 style="margin:0 0 16px;color:#171717;font-size:20px;font-weight:600;">{label}验证码</h1>
      <p style="margin:0 0 20px;color:#404040;font-size:14px;line-height:1.6;">请在页面中输入下面的验证码完成{label}：</p>
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
