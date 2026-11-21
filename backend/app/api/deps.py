"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.models import User

DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def current_reviewer(db: DbSession, x_reviewer: Annotated[str | None, Header()] = None):
    """The reviewer's name from the X-Reviewer header (URL-encoded); login comes later (M0.7)."""
    from urllib.parse import unquote

    from fastapi import HTTPException, status

    from app.review.service import reviewer

    name = unquote(x_reviewer or "").strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "검수자 이름이 필요합니다 (X-Reviewer 헤더)")
    return reviewer(db, name)


Reviewer = Annotated[User, Depends(current_reviewer)]
