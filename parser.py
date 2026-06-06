from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Any


DAILY_REPORT_INDEX_COLUMNS = [
    "date",
    "report_html_link",
    "report_csv_link",
    "funnel_type",
    "total_spend",
    "meta_complete_registration",
    "ghl_leads",
    "meta_cpl",
    "ghl_cpl",
    "booked_leads",
    "showed_leads",
    "closed_leads",
    "lead_to_booking_pct",
    "booking_to_show_pct",
    "show_to_close_pct",
    "unattributed_leads",
    "current_crm_total",
    "recommended_decision",
    "created_at",
]

DAILY_GHL_SUMMARY_COLUMNS = [
    "date",
    "executive_summary",
    "new_leads",
    "booked_leads",
    "showed_leads",
    "closed_leads",
    "lead_to_booking_pct",
    "booking_to_show_pct",
    "show_to_close_pct",
    "daily_ghl_leads",
    "unattributed_leads",
    "current_crm_total",
    "current_new",
    "current_booked",
    "current_showed",
    "current_closed",
    "current_unknown",
    "unassigned_count",
    "created_at",
]

DAILY_GHL_DIAGNOSIS_COLUMNS = [
    "date",
    "executive_summary",
    "daily_summary",
    "overall_status",
    "diagnosis_text",
    "created_at",
]

DAILY_GHL_STATUS_COLUMNS = [
    "date",
    "scope",
    "status",
    "count",
    "created_at",
]

DAILY_GHL_OWNER_COLUMNS = [
    "date",
    "owner_id",
    "owner_label",
    "total",
    "new",
    "booked",
    "showed",
    "closed",
    "unknown",
    "created_at",
]

DAILY_GHL_LANDING_COLUMNS = [
    "date",
    "landing_url",
    "lead_count",
    "created_at",
]

SHEET_TABS = {
    "daily_report_index": DAILY_REPORT_INDEX_COLUMNS,
    "daily_ghl_summary": DAILY_GHL_SUMMARY_COLUMNS,
    "daily_ghl_diagnosis": DAILY_GHL_DIAGNOSIS_COLUMNS,
    "daily_ghl_status": DAILY_GHL_STATUS_COLUMNS,
    "daily_ghl_owner": DAILY_GHL_OWNER_COLUMNS,
    "daily_ghl_landing": DAILY_GHL_LANDING_COLUMNS,
}


def build_historical_rows(
    *,
    report_date: date,
    summary: dict[str, Any],
    decision_report: dict[str, Any] | None,
    ga4_data: dict[str, Any] | None,
    meta_data: dict[str, Any] | None,
    report_links: dict[str, str] | None = None,
    created_at: datetime | None = None,
) -> dict[str, list[list[Any]]]:
    created_at_value = (
        created_at or datetime.now(ZoneInfo("Europe/Budapest"))
    ).isoformat(timespec="seconds")
    decision_report = decision_report or {}
    ghl = decision_report.get("ghl") or {}
    diagnosis = decision_report.get("diagnosis") or {}
    calculated = decision_report.get("calculated") or {}
    meta = decision_report.get("meta") or {}
    report_links = report_links or {}
    owner_rows = ghl.get("current_crm_by_owner", [])
    unassigned_count = sum(
        int(row.get("total", 0))
        for row in owner_rows
        if isinstance(row, dict) and str(row.get("owner_id")) == "unassigned"
    )
    daily_current_status = {
        str(row.get("status", "")).lower(): int(row.get("count", 0))
        for row in ghl.get("current_crm_by_status", [])
        if isinstance(row, dict)
    }
    daily_ghl_summary = _row_values(
        DAILY_GHL_SUMMARY_COLUMNS,
        {
            "date": report_date.isoformat(),
            "executive_summary": decision_report.get("executive_summary", ""),
            "new_leads": summary.get("new_leads", 0),
            "booked_leads": summary.get("booked_leads", 0),
            "showed_leads": summary.get("showed_leads", 0),
            "closed_leads": summary.get("closed_leads", 0),
            "lead_to_booking_pct": summary.get("lead_to_booking_pct", 0),
            "booking_to_show_pct": summary.get("booking_to_show_pct", 0),
            "show_to_close_pct": summary.get("show_to_close_pct", 0),
            "daily_ghl_leads": ghl.get("total_leads", summary.get("new_leads", 0)),
            "unattributed_leads": ghl.get("unattributed_leads", 0),
            "current_crm_total": ghl.get("current_crm_total", 0),
            "current_new": daily_current_status.get("new", 0),
            "current_booked": daily_current_status.get("booked", 0),
            "current_showed": daily_current_status.get("showed", 0),
            "current_closed": daily_current_status.get("closed", 0),
            "current_unknown": daily_current_status.get("unknown", 0),
            "unassigned_count": unassigned_count,
            "created_at": created_at_value,
        },
    )
    daily_report_index = _row_values(
        DAILY_REPORT_INDEX_COLUMNS,
        {
            "date": report_date.isoformat(),
            "report_html_link": report_links.get("html", ""),
            "report_csv_link": report_links.get("csv", ""),
            "funnel_type": decision_report.get("funnel_type", "landing"),
            "total_spend": meta.get("spend", 0),
            "meta_complete_registration": meta.get("registration_leads", 0),
            "ghl_leads": ghl.get("total_leads", summary.get("new_leads", 0)),
            "meta_cpl": calculated.get("meta_cpl", 0),
            "ghl_cpl": calculated.get("ghl_lead_cost", 0),
            "booked_leads": summary.get("booked_leads", 0),
            "showed_leads": summary.get("showed_leads", 0),
            "closed_leads": summary.get("closed_leads", 0),
            "lead_to_booking_pct": summary.get("lead_to_booking_pct", 0),
            "booking_to_show_pct": summary.get("booking_to_show_pct", 0),
            "show_to_close_pct": summary.get("show_to_close_pct", 0),
            "unattributed_leads": ghl.get("unattributed_leads", 0),
            "current_crm_total": ghl.get("current_crm_total", 0),
            "recommended_decision": diagnosis.get("daily_summary", decision_report.get("executive_summary", "")),
            "created_at": created_at_value,
        },
    )
    daily_ghl_diagnosis = _row_values(
        DAILY_GHL_DIAGNOSIS_COLUMNS,
        {
            "date": report_date.isoformat(),
            "executive_summary": decision_report.get("executive_summary", ""),
            "daily_summary": diagnosis.get("daily_summary", ""),
            "overall_status": diagnosis.get("overall_status", ""),
            "diagnosis_text": diagnosis.get("text", ""),
            "created_at": created_at_value,
        },
    )
    daily_ghl_status_rows = [
        _row_values(
            DAILY_GHL_STATUS_COLUMNS,
            {
                "date": report_date.isoformat(),
                "scope": "daily_new_leads",
                "status": row.get("label") or row.get("status", ""),
                "count": row.get("count", 0),
                "created_at": created_at_value,
            },
        )
        for row in ghl.get("by_status", [])
        if isinstance(row, dict)
    ]
    daily_ghl_status_rows.extend(
        _row_values(
            DAILY_GHL_STATUS_COLUMNS,
            {
                "date": report_date.isoformat(),
                "scope": "current_crm_total",
                "status": row.get("label") or row.get("status", ""),
                "count": row.get("count", 0),
                "created_at": created_at_value,
            },
        )
        for row in ghl.get("current_crm_by_status", [])
        if isinstance(row, dict)
    )
    daily_ghl_owner_rows = [
        _row_values(
            DAILY_GHL_OWNER_COLUMNS,
            {
                "date": report_date.isoformat(),
                "owner_id": row.get("owner_id", ""),
                "owner_label": row.get("owner_label", ""),
                "total": row.get("total", 0),
                "new": row.get("new", 0),
                "booked": row.get("booked", 0),
                "showed": row.get("showed", 0),
                "closed": row.get("closed", 0),
                "unknown": row.get("unknown", 0),
                "created_at": created_at_value,
            },
        )
        for row in ghl.get("current_crm_by_owner", [])
        if isinstance(row, dict)
    ]
    daily_ghl_landing_rows = [
        _row_values(
            DAILY_GHL_LANDING_COLUMNS,
            {
                "date": report_date.isoformat(),
                "landing_url": row.get("landing_url", ""),
                "lead_count": row.get("lead_count", 0),
                "created_at": created_at_value,
            },
        )
        for row in ghl.get("by_landing", [])
        if isinstance(row, dict)
    ]

    return {
        "daily_report_index": [daily_report_index],
        "daily_ghl_summary": [daily_ghl_summary],
        "daily_ghl_diagnosis": [daily_ghl_diagnosis],
        "daily_ghl_status": daily_ghl_status_rows,
        "daily_ghl_owner": daily_ghl_owner_rows,
        "daily_ghl_landing": daily_ghl_landing_rows,
    }


def _row_values(columns: list[str], values: dict[str, Any]) -> list[Any]:
    return [values.get(column, "") for column in columns]
