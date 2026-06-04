from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any


def meeting_date_iso(value: Any) -> str:
    parsed = parse_meeting_datetime(value)
    if parsed is None:
        return str(value or "")[:10]
    return parsed.date().isoformat()


def parse_meeting_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, (int, float)):
        timestamp = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    text = str(value).strip()
    if text.isdigit():
        timestamp = int(text)
        timestamp = timestamp / 1000 if timestamp > 10_000_000_000 else timestamp
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None
