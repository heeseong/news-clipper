"""Channel connections (spec 8: /api/channels, /api/channels/oauth/callback; plan 7.2).

Admin only in the spec; until login exists (M0.7) any named reviewer can connect a channel.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import DbSession, Reviewer
from app.models import Channel, OAuthSession
from app.publish import channels as svc
from app.publish.base import PublishError
from app.publish.crypto import TokenKeyMissing
from app.schemas.publish import ChannelOut, ChannelsOut, ConnectIn, ConnectOut

router = APIRouter(prefix="/api/channels", tags=["channels"])


def _out(row: OAuthSession, db) -> ConnectOut:
    ch = db.get(Channel, row.channel_id) if row.channel_id else None
    return ConnectOut(
        session_id=row.id, kind=row.kind, status=row.status, auth_url=svc.authorization_url(row) if row.status == "pending" else None,
        user_code=row.user_code, verification_url=row.verification_url, interval=row.interval, expires_at=row.expires_at,
        error=row.error, channel=ChannelOut.model_validate(ch) if ch else None,
    )  # fmt: skip


@router.get("", response_model=ChannelsOut)
def list_channels(db: DbSession) -> ChannelsOut:
    rows = db.scalars(select(Channel).order_by(Channel.status, Channel.name))
    return ChannelsOut(channels=[ChannelOut.model_validate(c) for c in rows], modes=svc.modes())


@router.post("/connect", response_model=ConnectOut)
def connect(body: ConnectIn, db: DbSession, user: Reviewer) -> ConnectOut:
    """Start connecting a channel: `device` (code to enter at google.com/device), `web` (open
    auth_url; Google comes back to /api/channels/oauth/callback) or `fake` (development)."""
    try:
        if body.mode == "fake":
            if "fake" not in svc.modes():
                raise svc.ChannelError("테스트 채널은 PUBLISHER=fake일 때만 만들 수 있습니다")
            ch = svc.connect_fake(db, user)
            db.commit()
            return ConnectOut(kind="fake", status="connected", channel=ChannelOut.model_validate(ch))
        row = svc.start(db, body.mode, user)
        db.commit()
        return _out(row, db)
    except (svc.ChannelError, TokenKeyMissing) as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except PublishError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e


@router.post("/connect/{session_id}/poll", response_model=ConnectOut)
def poll(session_id: str, db: DbSession) -> ConnectOut:
    """Device flow: the browser calls this every `interval` seconds until connected or failed."""
    row = db.get(OAuthSession, session_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "연결 요청을 찾을 수 없습니다")
    try:
        svc.poll(db, row)
    except TokenKeyMissing as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    db.commit()
    return _out(row, db)


@router.get("/oauth/callback", include_in_schema=False)
def oauth_callback(db: DbSession, state: str = "", code: str | None = None, error: str | None = None) -> RedirectResponse:
    """Google's redirect after the admin consents; back to the channels screen with the result."""
    try:
        row = svc.finish_web(db, state, code, error)
        db.commit()
    except (svc.ChannelError, TokenKeyMissing) as e:
        return RedirectResponse(f"/channels?error={quote(str(e))}", status.HTTP_303_SEE_OTHER)
    if row.status == "connected":
        return RedirectResponse(f"/channels?connected={row.channel_id}", status.HTTP_303_SEE_OTHER)
    return RedirectResponse(f"/channels?error={quote(row.error or '연결 실패')}", status.HTTP_303_SEE_OTHER)


@router.delete("/{channel_id}", response_model=ChannelOut)
def disconnect(channel_id: int, db: DbSession, user: Reviewer) -> ChannelOut:
    ch = db.get(Channel, channel_id)
    if ch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "채널을 찾을 수 없습니다")
    try:
        svc.disconnect(db, ch)
    except svc.ChannelError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    db.commit()
    return ChannelOut.model_validate(ch)
