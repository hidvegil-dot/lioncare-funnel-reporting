from __future__ import annotations

from datetime import date, datetime
from typing import Any


DAILY_SUMMARY_COLUMNS = [
    "date",
    "new_leads",
    "booked_leads",
    "showed_leads",
    "closed_leads",
    "lead_to_booking_pct",
    "booking_to_show_pct",
    "show_to_close_pct",
    "avg_meetings_per_closed",
    "median_meetings_per_closed",
    "closed_1_meeting",
    "closed_2_meetings",
    "closed_3plus_meetings",
    "created_at",
]

ADSET_DAILY_COLUMNS = [
    "date",
    "campaign_name",
    "adset_name",
    "decision",
    "spend",
    "landing_view",
    "complete_registration",
    "cpl",
    "click_to_landing_percent",
    "interpretation",
    "created_at",
]

GHL_STATUS_DAILY_COLUMNS = [
    "date",
    "total_ghl_contacts",
    "new_count",
    "booked_count",
    "showed_count",
    "closed_count",
    "unknown_count",
    "unassigned_count",
    "created_at",
]

SHEET_TABS = {
    "daily_summary": DAILY_SUMMARY_COLUMNS,
    "adset_daily": ADSET_DAILY_COLUMNS,
    "ghl_status_daily": GHL_STATUS_DAILY_COLUMNS,
}


def build_historical_rows(
    *,
    report_date: date,
    summary: dict[str, Any],
    decision_report: dict[str, Any] | None,
    ga4_data: dict[str, Any] | None,
    meta_data: dict[str, Any] | None,
    created_at: datetime | None = None,
) -> dict[str, list[list[Any]]]:
    created_at_value = (created_at or datetime.now()).isoformat(timespec="seconds")
    decision_report = decision_report or {}
    ghl = decision_report.get("ghl") or {}

    daily_summary = _row_values(
        DAILY_SUMMARY_COLUMNS,
        {
            "date": report_date.isoformat(),
            "new_leads": summary.get("new_leads", 0),
            "booked_leads": summary.get("booked_leads", 0),
            "showed_leads": summary.get("showed_leads", 0),
            "closed_leads": summary.get("closed_leads", 0),
            "lead_to_booking_pct": summary.get("lead_to_booking_pct", 0),
            "booking_to_show_pct": summary.get("booking_to_show_pct", 0),
            "show_to_close_pct": summary.get("show_to_close_pct", 0),
            "avg_meetings_per_closed": summary.get("avg_meetings_per_closed", 0),
            "median_meetings_per_closed": summary.get("median_meetings_per_closed", 0),
            "closed_1_meeting": summary.get("closed_1_meeting", 0),
            "closed_2_meetings": summary.get("closed_2_meetings", 0),
            "closed_3plus_meetings": summary.get("closed_3plus_meetings", 0),
            "created_at": created_at_value,
        },
    )

    status_map = {
        str(row.get("status", "")).lower(): int(row.get("count", 0))
        for row in ghl.get("by_status", [])
        if isinstance(row, dict)
    }
    owner_rows = ghl.get("current_crm_by_owner", [])
    unassigned_count = sum(
        int(row.get("total", 0))
        for row in owner_rows
        if isinstance(row, dict) and str(row.get("owner_id")) == "unassigned"
    )
    ghl_status_daily = _row_values(
        GHL_STATUS_DAILY_COLUMNS,
        {
            "date": report_date.isoformat(),
            "total_ghl_contacts": summary.get("new_leads", 0),
            "new_count": summary.get("new_leads", 0),
            "booked_count": summary.get("booked_leads", 0),
            "showed_count": summary.get("showed_leads", 0),
            "closed_count": summary.get("closed_leads", 0),
            "unknown_count": status_map.get("unknown", 0),
            "unassigned_count": unassigned_count,
            "created_at": created_at_value,
        },
    )

    return {
        "daily_summary": [daily_summary],
        "adset_daily": [],
        "ghl_status_daily": [ghl_status_daily],
    }


def _row_values(columns: list[str], values: dict[str, Any]) -> list[Any]:
    return [values.get(column, "") for column in columns]
