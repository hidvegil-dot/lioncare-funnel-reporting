from __future__ import annotations

from datetime import date, datetime
from typing import Any


DAILY_SUMMARY_COLUMNS = [
    "date",
    "total_spend",
    "meta_complete_registration",
    "ghl_leads",
    "meta_cpl",
    "ghl_cpl",
    "schedule_count",
    "contact_showed_count",
    "purchase_count",
    "purchase_value",
    "landing_page_views",
    "ga4_users",
    "ga4_thank_you_users",
    "meta_vs_ghl_diff_percent",
    "meta_vs_ga4_diff_percent",
    "measurement_status",
    "main_insight",
    "recommendation",
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
    meta = decision_report.get("meta") or {}
    calculated = decision_report.get("calculated") or {}
    diagnosis = decision_report.get("diagnosis") or {}
    ghl = decision_report.get("ghl") or {}
    meta_summary = (meta_data or {}).get("summary") or {}
    ga4_landing_summary = (ga4_data or {}).get("landing_performance", {}).get("summary", {})

    learning_events = {
        str(row.get("label", "")).lower(): row
        for row in meta.get("learning_events", [])
        if isinstance(row, dict)
    }

    daily_summary = _row_values(
        DAILY_SUMMARY_COLUMNS,
        {
            "date": report_date.isoformat(),
            "total_spend": meta.get("spend", meta_summary.get("spend", 0)),
            "meta_complete_registration": meta.get(
                "registration_leads",
                meta_summary.get("registration_leads", 0),
            ),
            "ghl_leads": ghl.get("total_leads", summary.get("new_leads", 0)),
            "meta_cpl": calculated.get("meta_cpl", 0),
            "ghl_cpl": calculated.get("ghl_lead_cost", 0),
            "schedule_count": _event_count(learning_events, "schedule"),
            "contact_showed_count": _event_count(learning_events, "contact"),
            "purchase_count": _event_count(learning_events, "purchase"),
            "purchase_value": _event_value(learning_events, "purchase"),
            "landing_page_views": meta.get("landing_page_view", meta_summary.get("landing_page_views", 0)),
            "ga4_users": ga4_landing_summary.get("users", 0),
            "ga4_thank_you_users": ga4_landing_summary.get("thank_you_users", 0),
            "meta_vs_ghl_diff_percent": calculated.get("meta_lead_vs_ghl_lead_delta_pct", 0),
            "meta_vs_ga4_diff_percent": calculated.get("meta_landing_vs_ga4_page_view_delta_pct", 0),
            "measurement_status": diagnosis.get("overall_status", ""),
            "main_insight": diagnosis.get("daily_summary") or diagnosis.get("text", ""),
            "recommendation": _recommendation_from_decision_report(decision_report),
            "created_at": created_at_value,
        },
    )

    adset_rows = [
        _row_values(
            ADSET_DAILY_COLUMNS,
            {
                "date": report_date.isoformat(),
                "campaign_name": adset.get("campaign_name", ""),
                "adset_name": adset.get("name", ""),
                "decision": adset.get("decision", ""),
                "spend": adset.get("spend", 0),
                "landing_view": adset.get("landing_page_views", 0),
                "complete_registration": adset.get("registration_leads", adset.get("meta_leads", 0)),
                "cpl": adset.get("cost_per_meta_conversion", 0),
                "click_to_landing_percent": adset.get("click_to_landing_pct", 0),
                "interpretation": adset.get("evaluation", ""),
                "created_at": created_at_value,
            },
        )
        for adset in meta.get("adsets", [])
        if isinstance(adset, dict)
    ]

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
            "total_ghl_contacts": ghl.get("total_leads", summary.get("new_leads", 0)),
            "new_count": status_map.get("new", 0),
            "booked_count": status_map.get("booked", 0),
            "showed_count": status_map.get("showed", 0),
            "closed_count": status_map.get("closed", 0),
            "unknown_count": status_map.get("unknown", 0),
            "unassigned_count": unassigned_count,
            "created_at": created_at_value,
        },
    )

    return {
        "daily_summary": [daily_summary],
        "adset_daily": adset_rows,
        "ghl_status_daily": [ghl_status_daily],
    }


def _row_values(columns: list[str], values: dict[str, Any]) -> list[Any]:
    return [values.get(column, "") for column in columns]


def _event_count(events: dict[str, dict[str, Any]], event_name: str) -> int:
    return int((events.get(event_name) or {}).get("count", 0))


def _event_value(events: dict[str, dict[str, Any]], event_name: str) -> float:
    return float((events.get(event_name) or {}).get("value", 0.0))


def _recommendation_from_decision_report(decision_report: dict[str, Any]) -> str:
    meta = decision_report.get("meta") or {}
    best = meta.get("best_adset") or {}
    worst = meta.get("worst_adset") or {}
    if best and worst and best.get("name") != worst.get("name"):
        return f"Erősítsd: {best.get('name')}; kontrolláld: {worst.get('name')}."
    if best:
        return f"Fő kontrollpont: {best.get('name')}."
    return ""
