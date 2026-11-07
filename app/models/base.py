"""SQLAlchemy Base。约束都使用模型里写明的名称。"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
