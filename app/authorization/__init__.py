"""授权模块。统一身份、统一授权、统一资源隔离、统一执行安全。"""

from __future__ import annotations

from app.authorization.domain.catalog import (
    DATA_SCOPES,
    PERMISSION_BY_CODE,
    PERMISSION_CATALOG,
    ROLE_BY_CODE,
    BUILTIN_ROLES,
    SUPER_ADMIN_ROLE_CODE,
)

__all__ = [
    "DATA_SCOPES",
    "PERMISSION_BY_CODE",
    "PERMISSION_CATALOG",
    "ROLE_BY_CODE",
    "BUILTIN_ROLES",
    "SUPER_ADMIN_ROLE_CODE",
]
