"""用 dybaseapi 换取临时凭证，再用 MNS 拉取短信回执。

发送验证码不走这里。队列名需在云通信控制台开通回执后获得。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime, timedelta

from app.core.config import Settings

logger = logging.getLogger("muzi_llm")
_REFRESH_EARLY = timedelta(minutes=2)


def token_needs_refresh(expire_time: str | None, now: datetime) -> bool:
    if not expire_time:
        return True
    expire = datetime.strptime(expire_time, "%Y-%m-%d %H:%M:%S")
    return expire <= now or expire - now < _REFRESH_EARLY


def pull_sms_reports(settings: Settings, token_state: dict[str, str | None]) -> int:
    """拉取一批回执并删除已读消息。返回本批条数。阻塞，调用方放到线程里。"""
    if not settings.sms_mns_queue_name:
        raise RuntimeError("SMS_MNS_QUEUE_NAME 未配置")
    from alibabacloud_dybaseapi20170525.client import Client as DybaseClient
    from alibabacloud_dybaseapi20170525.models import QueryTokenForMnsQueueRequest
    from alibabacloud_tea_openapi import models as open_api_models
    from alibabacloud_tea_util import models as util_models
    from mns.account import Account
    from mns.mns_exception import MNSExceptionBase

    expire_time = token_state.get("expire_time")
    if token_needs_refresh(expire_time, datetime.now()):
        config = open_api_models.Config(
            access_key_id=settings.sms_access_key_id,
            access_key_secret=settings.sms_access_key_secret,
        )
        config.endpoint = "dybaseapi.aliyuncs.com"
        client = DybaseClient(config)
        request = QueryTokenForMnsQueueRequest()
        request.message_type = settings.sms_mns_message_type or "SmsReport"
        request.queue_name = settings.sms_mns_queue_name
        response = client.query_token_for_mns_queue_with_options(request, util_models.RuntimeOptions())
        body = response.body
        if body is None or body.code != "OK" or body.message_token_dto is None:
            raise RuntimeError("获取短信回执队列凭证失败")
        sts = body.message_token_dto
        token_state["access_key_id"] = sts.access_key_id
        token_state["access_key_secret"] = sts.access_key_secret
        token_state["security_token"] = sts.security_token
        token_state["expire_time"] = sts.expire_time

    account = Account(
        settings.sms_mns_endpoint,
        token_state["access_key_id"],
        token_state["access_key_secret"],
        token_state["security_token"],
    )
    queue = account.get_queue(settings.sms_mns_queue_name)
    try:
        messages = queue.batch_receive_message(10, 3)
    except MNSExceptionBase as exc:
        if getattr(exc, "type", "") == "MessageNotExist":
            return 0
        raise
    handles: list[str] = []
    for message in messages:
        _log_report(getattr(message, "message_body", ""))
        handle = getattr(message, "receipt_handle", None)
        if handle:
            handles.append(handle)
    if handles:
        queue.batch_delete_message(handles)
    return len(messages)


async def run_receipt_loop(settings: Settings, stop: asyncio.Event) -> None:
    state: dict[str, str | None] = {}
    while not stop.is_set():
        try:
            count = await asyncio.to_thread(pull_sms_reports, settings, state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("短信回执拉取失败 error_type=%s", type(exc).__name__)
            count = 0
        if count == 0 and not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=10)
            except asyncio.TimeoutError:
                continue


def _log_report(raw: str) -> None:
    text = raw or ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        try:
            payload = json.loads(base64.b64decode(text).decode())
        except Exception:
            logger.warning("短信回执无法解析")
            return
    if isinstance(payload, list):
        items = payload
    else:
        items = [payload]
    for item in items:
        if not isinstance(item, dict):
            continue
        logger.info(
            "短信回执 biz_id=%s success=%s err_code=%s",
            item.get("biz_id", ""),
            item.get("success", ""),
            item.get("err_code", ""),
        )
