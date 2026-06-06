from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any


DEFAULT_EXCLUDED_PATTERNS = "webinar,webinár"
DEFAULT_EXCLUDED_END_DATE = "2026-05-16"


def filter_funnel_contacts(contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [contact for contact in contacts if not is_excluded_event_contact(contact)]


def filter_meta_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if not is_excluded_event_meta_row(row)]


def is_excluded_event_contact(contact: dict[str, Any]) -> bool:
    if not _within_excluded_date_window(contact):
        return False
    return _matches_excluded_patterns(contact)


def is_excluded_event_meta_row(row: dict[str, Any]) -> bool:
    row_date = _parse_date(row.get("date") or row.get("date_start") or row.get("report_date"))
    end_date = _excluded_event_end_date()
    if row_date is not None and end_date is not None and row_date > end_date:
        return False
    return _matches_excluded_patterns(row)


def infer_funnel_type_from_meta_row(row: dict[str, Any]) -> str:
    if _matches_excluded_patterns(row):
        return "webinar"
    return "landing"


def _within_excluded_date_window(contact: dict[str, Any]) -> bool:
    end_date = _excluded_event_end_date()
    if end_date is None:
        return True
    for field_name in ("lead_date", "created_date", "date_added", "createdAt"):
        parsed = _parse_date(contact.get(field_name))
        if parsed is not None:
            return parsed <= end_date
    return True


def _matches_excluded_patterns(value: Any) -> bool:
    patterns = _excluded_event_patterns()
    if not patterns:
        return False
    haystack = _stringify(value).lower()
    return any(pattern in haystack for pattern in patterns)


def _excluded_event_patterns() -> list[str]:
    raw = os.getenv("REPORT_EXCLUDED_LEAD_PATTERNS", DEFAULT_EXCLUDED_PATTERNS)
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _excluded_event_end_date() -> date | None:
    raw = os.getenv("REPORT_EXCLUDED_LEAD_END_DATE", DEFAULT_EXCLUDED_END_DATE).strip()
    if not raw:
        return None
    return _parse_date(raw)


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    for candidate in (text, text[:10], text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate).date()
        except ValueError:
            continue
    return None


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{_stringify(key)} {_stringify(item)}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(_stringify(item) for item in value)
    return str(value)
