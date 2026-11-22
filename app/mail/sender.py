"""通过标准 SMTP 下发验证码邮件。

刻意只依赖 smtplib：换供应商（QQ / 企业邮箱 / 阿里云 DirectMail）只改 .env，
业务代码不动。发送是阻塞 IO，调用方需用 asyncio.to_thread 包裹。

发信账号是一个有序池：主账号（MAIL_*）+ 可选的备用账号（MAIL_BACKUP_*）。
主账号连不上或认证失败时自动切到备用账号，并把故障账号跳过一小段时间，
避免每次发信都先撞一遍已经挂掉的账号。
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import time
from dataclasses import dataclass

from app.core.config import Settings
from app.core.exceptions import FeatureNotReadyError, MailDeliveryError
from app.mail.templates import build_code_message

logger = logging.getLogger("muzi_llm")


@dataclass(frozen=True)
class MailAccount:
    role: str
    host: str
    port: int
    username: str
    password: str
    from_addr: str
    use_ssl: bool
    start_tls: bool

    @property
    def key(self) -> str:
        # 不含密码：这份 key 会进日志和进程内健康表。
        return f"{self.role}:{self.host}:{self.username}"


# 进程内健康表：账号 key -> 恢复可用的单调时钟时间点。
# 放进程内而不是 Redis：发信是同步调用，不值得为它引入一次网络往返。
_unhealthy_until: dict[str, float] = {}


def reset_health() -> None:
    """清空故障记忆。测试用；也可以在账号修好后手动触发恢复。"""
    _unhealthy_until.clear()


def mail_configured(settings: Settings) -> bool:
    return bool(
        getattr(settings, "mail_enabled", False)
        and (settings.mail_primary_ready() or settings.mail_backup_ready())
    )


def mail_accounts(settings: Settings) -> list[MailAccount]:
    """按优先级返回可用的发信账号。主账号在前，备用账号在后。"""
    accounts: list[MailAccount] = []
    if settings.mail_primary_ready():
        accounts.append(
            MailAccount(
                role="primary",
                host=settings.mail_host,
                port=settings.mail_port,
                username=settings.mail_username,
                password=settings.mail_password,
                from_addr=settings.mail_from,
                use_ssl=settings.mail_use_ssl,
                start_tls=settings.mail_start_tls,
            )
        )
    if settings.mail_backup_ready():
        # QQ 这类邮箱要求 From 与登录账号一致，所以 From 留空时用用户名兜底。
        accounts.append(
            MailAccount(
                role="backup",
                host=settings.mail_backup_host,
                port=settings.mail_backup_port,
                username=settings.mail_backup_username,
                password=settings.mail_backup_password,
                from_addr=settings.mail_backup_from or settings.mail_backup_username,
                use_ssl=settings.mail_backup_use_ssl,
                start_tls=settings.mail_backup_start_tls,
            )
        )
    return accounts


def _healthy_key(account: MailAccount) -> str:
    return account.key


def _mark_failed(account: MailAccount, cooldown_seconds: int) -> None:
    if cooldown_seconds <= 0:
        return
    _unhealthy_until[_healthy_key(account)] = time.monotonic() + cooldown_seconds


def _mark_ok(account: MailAccount) -> None:
    _unhealthy_until.pop(_healthy_key(account), None)


def _in_cooldown(account: MailAccount) -> bool:
    until = _unhealthy_until.get(_healthy_key(account))
    if until is None:
        return False
    if until <= time.monotonic():
        # 冷却结束自动恢复，让修好的主账号重新抢回流量。
        _unhealthy_until.pop(_healthy_key(account), None)
        return False
    return True


def _is_terminal(exc: BaseException) -> bool:
    """收件人被拒绝是地址本身的问题，换发信账号也没用，直接失败。"""
    return isinstance(exc, smtplib.SMTPRecipientsRefused)


def _send_with(account: MailAccount, message, timeout: float) -> None:
    context = ssl.create_default_context()
    if account.use_ssl:
        client = smtplib.SMTP_SSL(account.host, account.port, timeout=timeout, context=context)
    else:
        client = smtplib.SMTP(account.host, account.port, timeout=timeout)
    with client:
        client.ehlo()
        if account.start_tls and not account.use_ssl:
            client.starttls(context=context)
            client.ehlo()
        if account.username:
            # QQ 邮箱这里必须用授权码，用登录密码会返回 535。
            client.login(account.username, account.password)
        client.send_message(message)


def send_verification_code(settings: Settings, email: str, code: str, scene: str = "login") -> None:
    """同步发送一封验证码邮件，失败时按账号池顺序切换。

    只有所有账号都失败才抛 MailDeliveryError，且只抛安全文案。
    """
    if not mail_configured(settings):
        raise FeatureNotReadyError("邮件服务尚未配置")

    accounts = mail_accounts(settings)
    if not accounts:
        raise FeatureNotReadyError("邮件服务尚未配置")

    domain = email.rsplit("@", 1)[-1]
    # 冷却中的账号先跳过；全部都在冷却时仍然按顺序试一遍，
    # 否则一次集中失败会让所有账号被永久拉黑。
    ordered = [account for account in accounts if not _in_cooldown(account)] or accounts
    last_error: BaseException | None = None

    for account in ordered:
        try:
            message = build_code_message(
                settings,
                email,
                code,
                scene=scene,
                from_addr=account.from_addr,
            )
            _send_with(account, message, settings.mail_timeout_seconds)
        except Exception as exc:
            last_error = exc
            if _is_terminal(exc):
                logger.warning(
                    "邮件收件人被拒绝 role=%s domain=%s error_type=%s",
                    account.role,
                    domain,
                    type(exc).__name__,
                )
                raise MailDeliveryError() from None
            _mark_failed(account, settings.mail_failover_cooldown_seconds)
            # 只记错误类型与账号角色：不落验证码、不落完整邮箱、不落 SMTP 响应正文。
            logger.warning(
                "邮件发送失败 role=%s domain=%s error_type=%s",
                account.role,
                domain,
                type(exc).__name__,
            )
            continue
        _mark_ok(account)
        logger.info("验证码邮件已受理 role=%s domain=%s scene=%s", account.role, domain, scene)
        return

    logger.warning(
        "邮件发送失败，账号池已用尽 accounts=%s domain=%s last=%s",
        [account.role for account in accounts],
        domain,
        type(last_error).__name__ if last_error else "none",
    )
    raise MailDeliveryError() from None
