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


@pytest.mark.parametrize(
    ("email", "username", "password"),
    [
        ("muzi", "muzi", "muzimuzi"),
        ("@gmail.com", "muzi", "muzimuzi"),
        ("muzi@", "muzi", "muzimuzi"),
        ("muzi@gmail.com", "1muzi", "muzimuzi"),
        ("muzi@gmail.com", "mu", "muzimuzi"),
        ("muzi@gmail.com", "Muzi-1", "muzimuzi"),
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
