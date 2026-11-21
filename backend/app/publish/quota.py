"""Daily API quota (spec 5.9, plan 7.4).

YouTube gives each Google Cloud project 10,000 units a day by default, reset at midnight Pacific
time. videos.insert costs 1,600 and thumbnails.set 50, so about six uploads a day until the quota
is raised. Units are reserved before an upload; when they would run out the upload waits for the
next quota day (and the dashboard shows it).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import QuotaUsage
from app.models.base import utcnow

PACIFIC = ZoneInfo("America/Los_Angeles")
KST = ZoneInfo("Asia/Seoul")


def quota_day(now: datetime | None = None) -> date:
    return (now or utcnow()).astimezone(PACIFIC).date()


def next_reset(now: datetime | None = None) -> datetime:
    """The next midnight in Pacific time, in UTC (SQLite drops time zones)."""
    day = quota_day(now) + timedelta(days=1)
    return datetime.combine(day, time(0, 0), PACIFIC).astimezone(UTC)


def kst(dt: datetime) -> str:
    return dt.astimezone(KST).strftime("%m-%d %H:%M")


def limit(platform: str) -> int | None:
    return get_settings().youtube_daily_quota if platform == "youtube" else None


def upload_cost(platform: str) -> int:
    s = get_settings()
    return s.youtube_quota_insert + s.youtube_quota_thumbnail if platform == "youtube" else 0


def _row(session: Session, platform: str, day: date, *, lock: bool = False) -> QuotaUsage:
    q = select(QuotaUsage).where(QuotaUsage.platform == platform, QuotaUsage.day == day)
    row = session.scalar(q.with_for_update() if lock else q)
    if row is None:
        row = QuotaUsage(platform=platform, day=day, units=0)
        session.add(row)
        session.flush()
    return row


def used(session: Session, platform: str, now: datetime | None = None) -> int:
    row = session.scalar(select(QuotaUsage).where(QuotaUsage.platform == platform, QuotaUsage.day == quota_day(now)))
    return row.units if row else 0


def reserve(session: Session, platform: str, units: int, now: datetime | None = None) -> bool:
    """Take `units` from today's quota; False (nothing taken) when they do not fit."""
    cap = limit(platform)
    if cap is None or units <= 0:
        return True
    row = _row(session, platform, quota_day(now), lock=True)
    if row.units + units > cap:
        return False
    row.units += units
    session.flush()
    return True


def add(session: Session, platform: str, units: int, now: datetime | None = None) -> None:
    if limit(platform) is None or units <= 0:
        return
    _row(session, platform, quota_day(now), lock=True).units += units
    session.flush()


def exhaust(session: Session, platform: str, now: datetime | None = None) -> None:
    """The platform said the quota is gone (403 quotaExceeded): stop trying until the reset."""
    cap = limit(platform)
    if cap is not None:
        row = _row(session, platform, quota_day(now), lock=True)
        row.units = max(row.units, cap)
        session.flush()
