"""注册字段规则。不连接数据库。"""

from __future__ import annotations

import pytest

from app.core.exceptions import InvalidRequestError
from app.services.auth_service import (
    normalize_display_name,
    normalize_email,
    normalize_password,
    normalize_username,
)


def test_normalize_email_and_username() -> None:
    assert normalize_email("  Muzi@Gmail.com ") == "muzi@gmail.com"
    assert normalize_username("  Muzi_01 ") == "muzi_01"
    assert normalize_display_name("  ") is None
    assert normalize_display_name(" 木子 ") == "木子"


def test_normalize_username_supports_chinese() -> None:
    """用户名允许中文：首字为汉字，可混排汉字、字母、数字与下划线。"""
    assert normalize_username("  小明 ") == "小明"
    assert normalize_username("木子") == "木子"
    assert normalize_username("木子01") == "木子01"
    assert normalize_username("木子_Muzi") == "木子_muzi"


@pytest.mark.parametrize(
    ("email", "username", "password"),
    [
        ("muzi", "muzi", "muzimuzi"),
        ("@gmail.com", "muzi", "muzimuzi"),
        ("muzi@", "muzi", "muzimuzi"),
        ("muzi@gmail.com", "1muzi", "muzimuzi"),
        # 长度下限 2：单字仍然非法
        ("muzi@gmail.com", "a", "muzimuzi"),
        ("muzi@gmail.com", "_muzi", "muzimuzi"),
        ("muzi@gmail.com", "muzi-1", "muzimuzi"),
        # 中文同样要遵守字符集与长度
        ("muzi@gmail.com", "1小明", "muzimuzi"),
        ("muzi@gmail.com", "小明！", "muzimuzi"),
        ("muzi@gmail.com", "木" * 33, "muzimuzi"),
        ("muzi@gmail.com", "muzi", "short"),
    ],
)
def test_reject_invalid_account_fields(email: str, username: str, password: str) -> None:
    with pytest.raises(InvalidRequestError):
        if email != "muzi@gmail.com":
            normalize_email(email)
            return
        if username != "muzi":
            normalize_username(username)
            return
        normalize_password(password)
