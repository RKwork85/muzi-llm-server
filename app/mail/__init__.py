"""邮箱验证码：SMTP 发送与邮件正文。"""

from app.mail.sender import mail_configured, send_verification_code
from app.mail.templates import build_code_message

__all__ = ["mail_configured", "send_verification_code", "build_code_message"]
