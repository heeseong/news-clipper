from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

from .base import TimestampMixin, str_enum


class Role(StrEnum):
    EDITOR = "editor"  # upload, review, edit metadata
    PUBLISHER = "publisher"  # editor + approve, schedule, retry/cancel uploads
    ADMIN = "admin"  # users, channels, newsroom integration, program profiles


class User(TimestampMixin, Base):
    """Login is not implemented yet (deferred from M0); the table exists so jobs can record their creator."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[Role] = mapped_column(str_enum(Role), default=Role.EDITOR)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
