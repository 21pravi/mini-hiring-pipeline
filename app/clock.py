"""All timestamps are stored as fixed-width UTC ISO strings so SQLite can compare them as text."""
from __future__ import annotations

from datetime import datetime, timezone

_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_FMT)


def from_iso(value: str) -> datetime:
    return datetime.strptime(value, _FMT).replace(tzinfo=timezone.utc)
