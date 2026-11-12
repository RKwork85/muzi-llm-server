"""调用阿里云短信 SendSms 下发验证码。

dybaseapi 与 MNS SDK 只负责回执队列，不负责发送。
"""

from __future__ import annotations

import json
import logging

from app.core.config import Settings
from app.core.exceptions import FeatureNotReadyError, SmsDeliveryError, SmsRateLimitError

logger = logging.getLogger("muzi_llm.sms")


def sms_configured(settings: Settings) -> bool:
    return bool(
        settings.sms_access_key_id
        and settings.sms_access_key_secret
        and settings.sms_sign_name
        and settings.sms_template_code
    )


def send_verification_code(settings: Settings, phone: str, code: str) -> None:
    if not sms_configured(settings):
        raise FeatureNotReadyError("短信服务尚未配置")
    try:
        from alibabacloud_dysmsapi20170525 import models as sms_models
        from alibabacloud_dysmsapi20170525.client import Client as DysmsClient
        from alibabacloud_tea_openapi import models as open_api_models
    except ImportError as exc:
        raise FeatureNotReadyError("短信发送 SDK 未安装") from exc

    config = open_api_models.Config(
        access_key_id=settings.sms_access_key_id,
        access_key_secret=settings.sms_access_key_secret,
    )
    config.endpoint = settings.sms_endpoint or "dysmsapi.aliyuncs.com"
    client = DysmsClient(config)
    request = sms_models.SendSmsRequest(
        phone_numbers=phone,
        sign_name=settings.sms_sign_name,
        template_code=settings.sms_template_code,
        template_param=json.dumps({settings.sms_template_param_key: code}, ensure_ascii=False),
    )
    try:
        response = client.send_sms(request)
    except Exception as exc:
        logger.warning("短信发送请求失败 error_type=%s", type(exc).__name__)
        raise SmsDeliveryError() from None

    body = getattr(response, "body", None)
    result_code = getattr(body, "code", None)
    if result_code == "OK":
        logger.info("短信已受理 biz_id=%s", getattr(body, "biz_id", ""))
        return
    logger.warning("短信发送被拒绝 result_code=%s", result_code)
    if result_code == "isv.BUSINESS_LIMIT_CONTROL":
        raise SmsRateLimitError()
    raise SmsDeliveryError()
