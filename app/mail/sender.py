"""通过标准 SMTP 下发验证码邮件。

刻意只依赖 smtplib：换供应商（QQ / 企业邮箱 / 阿里云 DirectMail）只改 .env，
业务代码不动。发送是阻塞 IO，调用方需用 asyncio.to_thread 包裹。
"""

from __future__ import annotations

import logging
import smtplib
import ssl

from app.core.config import Settings
from app.core.exceptions import FeatureNotReadyError, MailDeliveryError
from app.mail.templates import build_code_message

logger = logging.getLogger("muzi_llm")


def mail_configured(settings: Settings) -> bool:
    return bool(
        getattr(settings, "mail_enabled", False)
        and settings.mail_host
        and settings.mail_from
    )


def send_verification_code(settings: Settings, email: str, code: str, scene: str = "login") -> None:
    """同步发送一封验证码邮件。失败只抛安全文案，不向调用方暴露 SMTP 细节。"""
    if not mail_configured(settings):
        raise FeatureNotReadyError("邮件服务尚未配置")

    message = build_code_message(settings, email, code, scene=scene)
    context = ssl.create_default_context()
    try:
        if settings.mail_use_ssl:
            client = smtplib.SMTP_SSL(
                settings.mail_host,
                settings.mail_port,
                timeout=settings.mail_timeout_seconds,
                context=context,
            )
        else:
            client = smtplib.SMTP(
                settings.mail_host,
                settings.mail_port,
                timeout=settings.mail_timeout_seconds,
            )
        with client:
            client.ehlo()
            if settings.mail_start_tls and not settings.mail_use_ssl:
                client.starttls(context=context)
                client.ehlo()
            if settings.mail_username:
                # QQ 邮箱这里必须用授权码，用登录密码会返回 535。
                client.login(settings.mail_username, settings.mail_password)
            client.send_message(message)
    except Exception as exc:
        # 只记错误类型与收件域名：不落验证码、不落完整邮箱、不落 SMTP 响应正文。
        logger.warning(
            "邮件发送失败 error_type=%s domain=%s",
            type(exc).__name__,
            email.rsplit("@", 1)[-1],
        )
        raise MailDeliveryError() from None
    logger.info("验证码邮件已受理 domain=%s scene=%s", email.rsplit("@", 1)[-1], scene)
