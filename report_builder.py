from __future__ import annotations

import csv
import math
import os
import statistics
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from funnel_filters import infer_funnel_type_from_meta_row


FUNNEL_FIELDS = [
    "new_leads",
    "booked_leads",
    "showed_leads",
    "closed_leads",
]

MEETING_FIELDS = [
    "avg_meetings_per_closed",
    "median_meetings_per_closed",
    "closed_1_meeting",
    "closed_2_meetings",
    "closed_3plus_meetings",
]

SHOWED_STATUSES = {
    "showed",
    "show",
    "completed",
    "confirmed-show",
    "attended",
    "attended_meeting",
}

NO_SHOW_STATUSES = {
    "noshow",
    "no-show",
    "no_show",
    "not_showed",
    "did_not_show",
}

CANCELLED_STATUSES = {
    "cancelled",
    "canceled",
    "cancelled_by_user",
    "canceled_by_user",
}

BRAND_NAVY = colors.HexColor("#0E2A47")
BRAND_NAVY_SOFT = colors.HexColor("#17395F")
BRAND_GOLD = colors.HexColor("#D4AF59")
BRAND_GOLD_SOFT = colors.HexColor("#F1DEAF")
BRAND_CREAM = colors.HexColor("#F6EFE0")
BRAND_TEXT = colors.HexColor("#243447")
BRAND_MUTED = colors.HexColor("#6B7280")
PDF_FONT_NAME = "ArialUnicode"
PDF_FONT_BOLD_NAME = "ArialUnicodeBold"
PDF_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
]

PDF_COMPARISON_METRICS = [
    "new_leads",
    "booked_leads",
    "showed_leads",
    "closed_leads",
    "lead_to_booking_pct",
    "booking_to_show_pct",
    "show_to_close_pct",
]

PDF_METRIC_LABELS = {
    "new_leads": "Új leadek",
    "booked_leads": "Foglalások",
    "showed_leads": "Megjelentek",
    "closed_leads": "Szerződések",
    "lead_to_booking_pct": "Lead -> foglalás",
    "booking_to_show_pct": "Foglalás -> megjelent",
    "show_to_close_pct": "Megjelent -> szerződés",
}


def build_report_rows(
    contacts: list[dict[str, Any]],
    closed_meeting_counts: dict[str, int],
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    daily: dict[date, dict[str, Any]] = {}
    cursor = start_date
    while cursor <= end_date:
        daily[cursor] = _empty_daily_row(cursor)
        cursor += timedelta(days=1)

    daily_closed_counts: dict[date, list[int]] = {day: [] for day in daily}

    for contact in contacts:
        for key, metric in (
            ("lead_date", "new_leads"),
            ("first_booking_date", "booked_leads"),
            ("show_date", "showed_leads"),
            ("close_date", "closed_leads"),
        ):
            field_date = contact.get(key)
            if isinstance(field_date, date) and start_date <= field_date <= end_date:
                daily[field_date][metric] += 1

        close_date = contact.get("close_date")
        if isinstance(close_date, date) and start_date <= close_date <= end_date:
            meeting_count = closed_meeting_counts.get(contact["id"], 0)
            daily_closed_counts[close_date].append(meeting_count)

    for day, row in daily.items():
        _enrich_row_with_derived_metrics(row=row, meeting_counts=daily_closed_counts[day])

    return [daily[day] for day in sorted(daily)]


def summarize_period(rows: list[dict[str, Any]], closed_meeting_counts: dict[str, int]) -> dict[str, Any]:
    totals = Counter()
    for row in rows:
        for field in FUNNEL_FIELDS:
            totals[field] += row[field]

    meeting_counts = list(closed_meeting_counts.values())
    return _build_summary_from_totals(totals=totals, meeting_counts=meeting_counts)


def build_period_comparison(
    rows: list[dict[str, Any]],
    current_period_start: date,
    current_label: str,
    previous_label: str,
) -> dict[str, Any]:
    previous_rows = [row for row in rows if parse_row_date(row) < current_period_start]
    current_rows = [row for row in rows if parse_row_date(row) >= current_period_start]
    previous_summary = summarize_rows_only(previous_rows)
    current_summary = summarize_rows_only(current_rows)
    comparison_rows = []
    for field in FUNNEL_FIELDS + [
        "lead_to_booking_pct",
        "booking_to_show_pct",
        "show_to_close_pct",
    ] + MEETING_FIELDS:
        current_value = current_summary[field]
        previous_value = previous_summary[field]
        delta = round(current_value - previous_value, 2)
        pct_change = compute_pct_change(current_value=current_value, previous_value=previous_value)
        direction = "flat"
        if delta > 0:
            direction = "up"
        elif delta < 0:
            direction = "down"
        comparison_rows.append(
            {
                "metric": field,
                "current": current_value,
                "previous": previous_value,
                "delta": delta,
                "pct_change": pct_change,
                "direction": direction,
            }
        )

    comparison = {
        "current_period": {
            "label": current_label,
            "summary": current_summary,
            "rows": current_rows,
        },
        "previous_period": {
            "label": previous_label,
            "summary": previous_summary,
            "rows": previous_rows,
        },
        "comparison_rows": comparison_rows,
    }
    comparison["primary_cards"] = build_primary_cards(comparison_rows)
    return comparison


def build_weekly_comparison(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) < 14:
        raise ValueError("Weekly comparison requires 14 days of daily rows")
    return build_period_comparison(
        rows=rows,
        current_period_start=parse_row_date(rows[7]),
        current_label=f"{rows[7]['date']} to {rows[-1]['date']}",
        previous_label=f"{rows[0]['date']} to {rows[6]['date']}",
    )


def overlay_funnel_counts_from_appointments(rows: list[dict[str, Any]], appointments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    booked_by_date: Counter[str] = Counter()
    showed_by_date: Counter[str] = Counter()
    for appointment in appointments:
        if appointment.get("deleted"):
            continue
        appointment_date = _extract_appointment_date(appointment)
        if appointment_date is None:
            continue
        booked_by_date[appointment_date.isoformat()] += 1
        status = str(
            appointment.get("appointmentStatus")
            or appointment.get("status")
            or appointment.get("calendarStatus")
            or appointment.get("appoinmentStatus")
            or ""
        ).strip().lower()
        if status not in SHOWED_STATUSES:
            continue
        showed_by_date[appointment_date.isoformat()] += 1

    for row in rows:
        row["booked_leads"] = booked_by_date.get(str(row["date"]), 0)
        row["showed_leads"] = showed_by_date.get(str(row["date"]), 0)
        meeting_counts = list(row.get("_meeting_counts", []))
        _enrich_row_with_derived_metrics(row=row, meeting_counts=meeting_counts)

    return rows


def build_landing_lead_cards(
    contacts: list[dict[str, Any]],
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for contact in contacts:
        lead_date = _contact_lead_date(contact)
        if not isinstance(lead_date, date) or not (start_date <= lead_date <= end_date):
            continue
        landing_url = contact.get("landing_page_url")
        if not landing_url:
            continue
        counts[str(landing_url)] += 1

    cards = []
    for landing_url, value in counts.most_common():
        cards.append(
            {
                "title": _landing_card_title(landing_url),
                "value": value,
                "subtitle": landing_url,
            }
        )
    return cards


def build_daily_decision_report(
    *,
    report_date: date,
    summary: dict[str, Any],
    ga4_data: dict[str, Any] | None,
    meta_data: dict[str, Any] | None,
    contacts: list[dict[str, Any]],
    current_crm_contacts: list[dict[str, Any]],
) -> dict[str, Any]:
    ghl_by_landing = _build_ghl_landing_rows(contacts=contacts, start_date=report_date, end_date=report_date)
    ghl_total = sum(row["lead_count"] for row in ghl_by_landing)
    ghl_status_rows = _build_ghl_status_rows(contacts=contacts, start_date=report_date, end_date=report_date)
    current_crm_status_rows = _build_current_crm_status_rows(current_crm_contacts)
    unattributed_leads = _count_unattributed_leads(contacts=contacts, start_date=report_date, end_date=report_date)

    landing_rows = (ga4_data or {}).get("landing_performance", {}).get("rows", [])
    meta_summary = (meta_data or {}).get("summary") or {}
    ga4_summary = (ga4_data or {}).get("landing_performance", {}).get("summary") or {}
    meta_adsets = _build_meta_adset_rows((meta_data or {}).get("adsets") or [])
    funnel_type = _infer_daily_funnel_type(meta_adsets)

    link_click = int(meta_summary.get("link_click", 0))
    landing_page_view = int(meta_summary.get("landing_page_views", 0))
    meta_form_leads = int(meta_summary.get("meta_form_leads", meta_summary.get("leads", 0)))
    meta_pixel_leads = int(meta_summary.get("leads", 0))
    registration_leads = int(meta_summary.get("registration_leads", 0))
    meta_leads = _select_primary_meta_leads(
        funnel_type=funnel_type,
        meta_form_leads=meta_form_leads,
        registration_leads=registration_leads,
    )
    meta_lead_label = _primary_meta_lead_label(funnel_type)
    ga4_page_view = int(ga4_summary.get("page_view", 0))
    ga4_thank_you_page_view = int(ga4_summary.get("thank_you_users", ga4_summary.get("thank_you_page_view", 0)))
    spend = float(meta_summary.get("spend", 0.0))

    meta_vs_ghl_delta_pct = _difference_pct(meta_leads, ghl_total)
    meta_vs_ga4_delta_pct = _difference_pct(landing_page_view, ga4_page_view)
    meta_vs_ga4_thank_you_delta_pct = _difference_pct(meta_leads, ga4_thank_you_page_view)
    diagnosis_status, diagnosis_text = _build_measurement_diagnosis(
        funnel_type=funnel_type,
        meta_vs_ga4_delta_pct=meta_vs_ga4_delta_pct,
        meta_vs_ga4_thank_you_delta_pct=meta_vs_ga4_thank_you_delta_pct,
        meta_vs_ghl_delta_pct=meta_vs_ghl_delta_pct,
        link_click=link_click,
        ga4_page_view=ga4_page_view,
        ghl_total=ghl_total,
    )

    best_adset = _select_best_adset(meta_adsets)
    worst_adset = _select_worst_adset(meta_adsets)
    if best_adset and worst_adset and best_adset.get("name") == worst_adset.get("name"):
        worst_adset = None

    return {
        "report_date": report_date.isoformat(),
        "funnel_type": funnel_type,
        "meta": {
            "spend": round(spend, 2),
            "impressions": int(meta_summary.get("impressions", 0)),
            "link_click": link_click,
            "landing_page_view": landing_page_view,
            "leads": meta_leads,
            "attributed_leads": meta_leads,
            "lead_label": meta_lead_label,
            "meta_form_leads": meta_form_leads,
            "pixel_leads": meta_pixel_leads,
            "registration_leads": registration_leads,
            "ctr": round(float(meta_summary.get("ctr", 0.0)), 2),
            "cpc": round(float(meta_summary.get("cpc", 0.0)), 2),
            "adsets": meta_adsets,
            "best_adset": best_adset,
            "worst_adset": worst_adset,
            "learning_events": _build_meta_learning_event_rows(meta_summary),
        },
        "landing": landing_rows,
        "ghl": {
            "new_leads": int(summary.get("new_leads", 0)),
            "total_leads": ghl_total,
            "by_landing": ghl_by_landing,
            "by_status": ghl_status_rows,
            "unattributed_leads": unattributed_leads,
            "current_crm_total": sum(int(row["count"]) for row in current_crm_status_rows),
            "current_crm_by_status": current_crm_status_rows,
            "current_crm_by_owner": _build_current_crm_by_owner_rows(current_crm_contacts),
        },
        "calculated": {
            "click_to_landing_pct": safe_pct(landing_page_view, link_click),
            "landing_to_lead_pct": safe_pct(ghl_total, landing_page_view),
            "meta_lead_vs_ghl_lead_delta_pct": meta_vs_ghl_delta_pct,
            "meta_landing_vs_ga4_page_view_delta_pct": meta_vs_ga4_delta_pct,
            "meta_lead_vs_ga4_thank_you_delta_pct": meta_vs_ga4_thank_you_delta_pct,
            "meta_cpl": round(spend / meta_leads, 2) if meta_leads else 0.0,
            "ghl_lead_cost": round(spend / ghl_total, 2) if ghl_total else 0.0,
        },
        "kpi_statuses": _build_daily_kpi_statuses(
            funnel_type=funnel_type,
            spend=spend,
            link_click=link_click,
            landing_page_view=landing_page_view,
            ghl_total=ghl_total,
            meta_leads=meta_leads,
            ctr=round(float(meta_summary.get("ctr", 0.0)), 2),
            booked_leads=int(summary.get("booked_leads", 0)),
            showed_leads=int(summary.get("showed_leads", 0)),
            closed_leads=int(summary.get("closed_leads", 0)),
        ),
        "diagnosis": {
            "meta_lead_vs_ghl_lead": "OK" if abs(meta_vs_ghl_delta_pct) <= 20 else "ATTRIBÚCIÓS ELTÉRÉS",
            "meta_landing_vs_ga4_page_view": (
                "N/A" if funnel_type == "webinar" else "OK" if abs(meta_vs_ga4_delta_pct) <= 20 else "DEFINÍCIÓS ELTÉRÉS"
            ),
            "meta_lead_vs_ga4_thank_you": (
                "N/A" if funnel_type == "webinar" else "OK" if abs(meta_vs_ga4_thank_you_delta_pct) <= 20 else "ATTRIBÚCIÓS ELTÉRÉS"
            ),
            "overall_status": diagnosis_status,
            "text": diagnosis_text,
            "daily_summary": _build_daily_summary_text(
                click_to_landing_pct=safe_pct(landing_page_view, link_click),
                landing_to_lead_pct=safe_pct(ghl_total, landing_page_view),
                unattributed_leads=unattributed_leads,
                meta_vs_ga4_delta_pct=meta_vs_ga4_delta_pct,
                meta_vs_ghl_delta_pct=meta_vs_ghl_delta_pct,
                meta_vs_ga4_thank_you_delta_pct=meta_vs_ga4_thank_you_delta_pct,
                best_adset=best_adset,
                worst_adset=worst_adset,
            ),
        },
        "executive_summary": _build_daily_executive_summary(
            funnel_type=funnel_type,
            spend=spend,
            meta_leads=meta_leads,
            ghl_total=ghl_total,
            booked_leads=int(summary.get("booked_leads", 0)),
            showed_leads=int(summary.get("showed_leads", 0)),
            closed_leads=int(summary.get("closed_leads", 0)),
            best_adset=best_adset,
        ),
    }


def _build_ghl_status_rows(
    *,
    contacts: list[dict[str, Any]],
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for contact in contacts:
        lead_date = _contact_lead_date(contact)
        if not isinstance(lead_date, date) or not (start_date <= lead_date <= end_date):
            continue
        status = str(contact.get("lead_status") or "unknown").strip().lower()
        counts[status] += 1

    ordered_statuses = ["new", "booked", "showed", "closed", "unknown"]
    rows = [
        {"status": status, "status_label": _status_label(status), "count": counts.get(status, 0)}
        for status in ordered_statuses
        if counts.get(status, 0)
    ]
    for status, count in counts.items():
        if status not in ordered_statuses:
            rows.append({"status": status, "status_label": _status_label(status), "count": count})
    return rows


def _build_current_crm_status_rows(contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for contact in contacts:
        status = str(contact.get("lead_status") or "unknown").strip().lower()
        counts[status] += 1

    ordered_statuses = ["new", "booked", "showed", "closed", "unknown"]
    rows = [
        {"status": status, "status_label": _status_label(status), "count": counts.get(status, 0)}
        for status in ordered_statuses
        if counts.get(status, 0)
    ]
    for status, count in counts.items():
        if status not in ordered_statuses:
            rows.append({"status": status, "status_label": _status_label(status), "count": count})
    return rows


def build_period_ghl_status_comparison(
    *,
    contacts: list[dict[str, Any]],
    previous_start: date,
    previous_end: date,
    current_start: date,
    current_end: date,
) -> dict[str, Any]:
    previous_rows = _build_ghl_status_rows(contacts=contacts, start_date=previous_start, end_date=previous_end)
    current_rows = _build_ghl_status_rows(contacts=contacts, start_date=current_start, end_date=current_end)

    previous_map = {str(row["status"]): int(row["count"]) for row in previous_rows}
    current_map = {str(row["status"]): int(row["count"]) for row in current_rows}
    ordered_statuses = ["new", "booked", "showed", "closed", "unknown"]
    seen = set(ordered_statuses) | set(previous_map) | set(current_map)

    rows: list[dict[str, Any]] = []
    for status in ordered_statuses + sorted(seen - set(ordered_statuses)):
        current_count = current_map.get(status, 0)
        previous_count = previous_map.get(status, 0)
        if current_count == 0 and previous_count == 0:
            continue
        delta = current_count - previous_count
        rows.append(
            {
                "status": status,
                "status_label": _status_label(status),
                "current": current_count,
                "previous": previous_count,
                "delta": delta,
                "pct_change": compute_pct_change(current_count, previous_count),
                "direction": _direction_from_delta(delta),
            }
        )

    return {
        "previous_label": f"{previous_start.isoformat()} to {previous_end.isoformat()}",
        "current_label": f"{current_start.isoformat()} to {current_end.isoformat()}",
        "rows": rows,
    }


def _build_current_crm_by_owner_rows(contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    user_labels = _load_user_labels()
    rows_by_owner: dict[str, dict[str, Any]] = {}

    for contact in contacts:
        raw = contact.get("raw") or {}
        owner_id = str(raw.get("assignedTo") or "").strip() or "unassigned"
        owner_label = _owner_display_label(
            user_labels.get(owner_id, "Nincs delegálva" if owner_id == "unassigned" else owner_id)
        )
        row = rows_by_owner.setdefault(
            owner_id,
            {
                "owner_id": owner_id,
                "owner_label": owner_label,
                "total": 0,
                "new": 0,
                "booked": 0,
                "showed": 0,
                "closed": 0,
                "unknown": 0,
                "open": 0,
                "lost": 0,
                "won": 0,
            },
        )
        row["total"] += 1

        lead_status = str(contact.get("lead_status") or "").strip().lower()
        if lead_status in {"new", "booked", "showed", "closed"}:
            row[lead_status] += 1
        else:
            row["unknown"] += 1

        opportunities = raw.get("opportunities") or raw.get("opportunity") or []
        if isinstance(opportunities, dict):
            opportunities = [opportunities]
        for opportunity in opportunities:
            if not isinstance(opportunity, dict):
                continue
            status = str(opportunity.get("status") or "").strip().lower()
            if status in {"open", "lost", "won"}:
                row[status] += 1

    rows = list(rows_by_owner.values())
    rows.sort(key=lambda row: (row["owner_id"] == "unassigned", -row["total"], row["owner_label"]))
    return rows


def _owner_display_label(label: str) -> str:
    mapping = {
        "László Hidvégi": "Hidvégi László",
        "Amelita Gulyás": "Gulyás Amelita",
        "Nincs owner": "Nincs delegálva",
        "unassigned": "Nincs delegálva",
    }
    return mapping.get(label, label)


def _count_unattributed_leads(
    *,
    contacts: list[dict[str, Any]],
    start_date: date,
    end_date: date,
) -> int:
    total = 0
    for contact in contacts:
        lead_date = _contact_lead_date(contact)
        if not isinstance(lead_date, date) or not (start_date <= lead_date <= end_date):
            continue
        if not contact.get("landing_page_url"):
            total += 1
    return total


def _status_label(status: str) -> str:
    mapping = {
        "new": "Új",
        "booked": "Foglalt",
        "showed": "Megjelent",
        "closed": "Lezárt",
        "unknown": "Ismeretlen",
    }
    return mapping.get(status, status)


def _contact_lead_date(contact: dict[str, Any]) -> date | None:
    lead_date = contact.get("lead_date") or contact.get("created_date")
    return lead_date if isinstance(lead_date, date) else None


def _infer_daily_funnel_type(meta_adsets: list[dict[str, Any]]) -> str:
    funnel_types = {str(row.get("funnel_type") or "landing") for row in meta_adsets}
    if "webinar" in funnel_types and "landing" in funnel_types:
        return "mixed"
    if "webinar" in funnel_types:
        return "webinar"
    return "landing"


def _funnel_type_label(funnel_type: str) -> str:
    mapping = {
        "landing": "Landing",
        "webinar": "Webinár űrlap",
        "mixed": "Vegyes",
    }
    return mapping.get(funnel_type, funnel_type)


def _select_primary_meta_leads(*, funnel_type: str, meta_form_leads: int, registration_leads: int) -> int:
    if funnel_type == "webinar":
        return meta_form_leads
    if funnel_type == "mixed":
        return max(meta_form_leads, registration_leads)
    return registration_leads


def _primary_meta_lead_label(funnel_type: str) -> str:
    if funnel_type == "webinar":
        return "Meta űrlap lead"
    if funnel_type == "mixed":
        return "Meta lead jel"
    return "Meta CompleteRegistration"


def _build_measurement_diagnosis(
    *,
    funnel_type: str,
    meta_vs_ga4_delta_pct: float,
    meta_vs_ga4_thank_you_delta_pct: float,
    meta_vs_ghl_delta_pct: float,
    link_click: int,
    ga4_page_view: int,
    ghl_total: int,
) -> tuple[str, str]:
    if funnel_type == "webinar":
        if abs(meta_vs_ghl_delta_pct) > 20:
            return (
                "ATTRIBÚCIÓS ELTÉRÉS",
                "Webinár instant form funnel: landing és GA4 thank-you kontroll nem releváns, a fő egyezést a Meta űrlap lead és a GHL lead között kell nézni.",
            )
        return (
            "WEBINÁR_FORM",
            "Webinár instant form funnel: landing view, click → landing és GA4 thank-you hiány nem hiba. A fő kontroll a Meta űrlap lead és a GHL-be bejutott webinár lead.",
        )

    if abs(meta_vs_ga4_delta_pct) > 20:
        return (
            "DEFINÍCIÓS ELTÉRÉS",
            "A Meta landing view és a GA4 összes landing page view nem ugyanazt a kört méri: a GA4 minden forrást és minden beállított landinget tartalmaz, ezért ezt definíciós eltérésként kell kezelni.",
        )
    if abs(meta_vs_ga4_thank_you_delta_pct) > 20:
        return (
            "ATTRIBÚCIÓS ELTÉRÉS",
            "A Meta attribúciós Lead és a GA4 thank-you users jelentősen eltér, ezért ezt attribúciós eltérésként kell kezelni, nem tiszta webes befejezésként.",
        )
    if abs(meta_vs_ghl_delta_pct) > 20:
        return (
            "ATTRIBÚCIÓS ELTÉRÉS",
            "A Meta attribúciós Lead és a GHL új lead jelentősen eltér, ezért a Meta hirdetési attribúció és a CRM új lead definíció nem ugyanaz.",
        )
    if link_click and not ga4_page_view:
        return ("BEAVATKOZÁS", "Van hirdetéskattintás, de nincs mérhető landing forgalom, ezért a landing mérés hibás.")
    if ga4_page_view and not ghl_total:
        return ("BEAVATKOZÁS", "Van landing forgalom, de nincs GHL lead, ezért a landing vagy az űrlap teljesít gyengén.")
    return ("OK", "Az aznapi Meta, GA4 és GHL számok közel azonos képet adnak, a mérés jelenleg konzisztens.")


def _build_meta_adset_rows(adsets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for adset in sorted(adsets, key=lambda row: float(row.get("spend", 0.0)), reverse=True):
        spend = float(adset.get("spend", 0.0))
        link_click = int(adset.get("link_click", 0))
        landing_page_views = int(adset.get("landing_page_views", 0))
        registration_leads = int(adset.get("registration_leads", 0))
        meta_form_leads = int(adset.get("meta_form_leads", adset.get("leads", 0)))
        pixel_leads = int(adset.get("leads", 0))
        funnel_type = infer_funnel_type_from_meta_row(adset)
        meta_leads = _select_primary_meta_leads(
            funnel_type=funnel_type,
            meta_form_leads=meta_form_leads,
            registration_leads=registration_leads,
        )
        rows.append(
            {
                "name": adset.get("name") or adset.get("adset_name") or "Ismeretlen hirdetéssorozat",
                "funnel_type": funnel_type,
                "funnel_type_label": _funnel_type_label(funnel_type),
                "spend": round(spend, 2),
                "impressions": int(adset.get("impressions", 0)),
                "link_click": link_click,
                "landing_page_views": landing_page_views,
                "meta_leads": meta_leads,
                "lead_label": _primary_meta_lead_label(funnel_type),
                "meta_form_leads": meta_form_leads,
                "registration_leads": registration_leads,
                "pixel_leads": pixel_leads,
                "ctr": round(float(adset.get("ctr", 0.0)), 2),
                "cpc": round(spend / link_click, 2) if link_click else 0.0,
                "click_to_landing_pct": safe_pct(landing_page_views, link_click),
                "cost_per_meta_conversion": round(spend / meta_leads, 2) if meta_leads else 0.0,
                "evaluation": _evaluate_meta_adset(
                    funnel_type=funnel_type,
                    spend=spend,
                    link_click=link_click,
                    landing_page_views=landing_page_views,
                    registration_leads=meta_leads,
                ),
                "decision": _decide_meta_adset_action(
                    funnel_type=funnel_type,
                    spend=spend,
                    landing_page_views=landing_page_views,
                    registration_leads=meta_leads,
                ),
            }
        )
    return rows


def _build_meta_learning_event_rows(meta_summary: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "label": "CompleteRegistration",
            "count": int(meta_summary.get("registration_leads", 0)),
            "value": round(float(meta_summary.get("registration_value", 0.0)), 2),
            "meaning": "Lead minősítő visszajelzés",
        },
        {
            "label": "Schedule",
            "count": int(meta_summary.get("schedule_events", 0)),
            "value": round(float(meta_summary.get("schedule_value", 0.0)), 2),
            "meaning": "Időpontot foglalt",
        },
        {
            "label": "Contact",
            "count": int(meta_summary.get("contact_events", 0)),
            "value": round(float(meta_summary.get("contact_value", 0.0)), 2),
            "meaning": "Megjelent a meetingen",
        },
        {
            "label": "Purchase",
            "count": int(meta_summary.get("purchase_events", 0)),
            "value": round(float(meta_summary.get("purchase_value", 0.0)), 2),
            "meaning": "Szerződés / vásárlás",
        },
    ]


def _decide_meta_adset_action(
    *,
    funnel_type: str = "landing",
    spend: float,
    landing_page_views: int,
    registration_leads: int,
) -> str:
    cpl = round(spend / registration_leads, 2) if registration_leads else 0.0
    if registration_leads >= 2 and cpl <= 4000:
        return "SKÁLÁZD ÓVATOSAN"
    if registration_leads > 0:
        return "TARTSD"
    if spend >= 5000 and registration_leads == 0:
        return "ÁLLÍTSD LE / CSERÉLD"
    if funnel_type != "webinar" and spend >= 3000 and landing_page_views >= 15 and registration_leads == 0:
        return "FIGYELD SZOROSAN"
    return "ADATGYŰJTÉS"


def _build_daily_executive_summary(
    *,
    funnel_type: str = "landing",
    spend: float,
    meta_leads: int,
    ghl_total: int,
    booked_leads: int,
    showed_leads: int,
    closed_leads: int,
    best_adset: dict[str, Any] | None,
) -> str:
    best_name = best_adset.get("name") if best_adset else "nincs elég adat"
    if closed_leads:
        return f"A nap végén {closed_leads} szerződésig jutott lead volt; a legerősebb Meta sorozat: {best_name}."
    if showed_leads:
        return f"A nap fő eredménye {showed_leads} megjelent meeting; a Meta tanulási fókusz továbbra is a CompleteRegistration minősége."
    if booked_leads:
        return f"{booked_leads} foglalás született, de szerződés még nem; a következő kontrollpont a meeting-megjelenés."
    if ghl_total:
        return f"{ghl_total} GHL lead érkezett {round(spend, 0):.0f} Ft költésből; most a leadből foglalásba lépést kell figyelni."
    if meta_leads:
        return f"Meta oldalon {meta_leads} {_primary_meta_lead_label(funnel_type)} látszik, de GHL lead nincs; ezt attribúciós kontrollként kell kezelni."
    return "Nem volt érdemi leadmozgás; a nap inkább adatgyűjtési és mérési kontroll nap."


def _select_best_adset(adsets: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [adset for adset in adsets if int(adset.get("meta_leads", 0)) > 0]
    if candidates:
        return min(
            candidates,
            key=lambda adset: (
                float(adset.get("cost_per_meta_conversion", 0.0)),
                -int(adset.get("meta_leads", 0)),
            ),
        )
    if adsets:
        return max(adsets, key=lambda adset: int(adset.get("landing_page_views", 0)))
    return None


def _select_worst_adset(adsets: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [adset for adset in adsets if float(adset.get("spend", 0.0)) > 0]
    if not candidates:
        return None
    zero_conversion = [adset for adset in candidates if int(adset.get("meta_leads", 0)) == 0]
    if zero_conversion:
        return max(
            zero_conversion,
            key=lambda adset: (
                float(adset.get("spend", 0.0)),
                -float(adset.get("click_to_landing_pct", 0.0)),
            ),
        )
    return max(candidates, key=lambda adset: float(adset.get("cost_per_meta_conversion", 0.0)))


def _build_daily_summary_text(
    *,
    click_to_landing_pct: float,
    landing_to_lead_pct: float,
    unattributed_leads: int,
    meta_vs_ga4_delta_pct: float,
    meta_vs_ghl_delta_pct: float,
    meta_vs_ga4_thank_you_delta_pct: float,
    best_adset: dict[str, Any] | None,
    worst_adset: dict[str, Any] | None,
) -> str:
    if abs(meta_vs_ga4_delta_pct) > 20:
        return "A Meta landing view és a GA4 összes landing page view eltér, mert nem ugyanazt a kört mérik; a lead egyezést külön kell nézni."
    if abs(meta_vs_ga4_thank_you_delta_pct) > 20:
        return "A Meta attribúciós Lead nincs összhangban a thank-you oldallal, ezért ezt ne tiszta tracking hibának, hanem attribúciós eltérésnek tekintsd."
    if unattributed_leads > 0:
        return f"Van {unattributed_leads} attribúció nélküli GHL lead, ezért a CRM forrásmérés javítása elsődleges."
    if landing_to_lead_pct < 3:
        return "A forgalom átjut a landingre, de a lead konverzió gyenge, ezért a landing vagy az ajánlat igényel beavatkozást."
    if click_to_landing_pct < 60:
        return "A kattintásból túl kevés landing view lesz, ezért landing vagy mérési probléma valószínű."
    if abs(meta_vs_ghl_delta_pct) > 20:
        return "A Meta attribúciós Lead és a GHL új lead eltér, ezért a Meta és a CRM nem ugyanazt a konverziót számolja."
    if best_adset and worst_adset and best_adset.get("name") != worst_adset.get("name"):
        return f"A nap nyertese: {best_adset['name']}; a leggyengébb pont: {worst_adset['name']}."
    return "A napi funnel stabil, a fő számok alapján nincs azonnali beavatkozási kényszer."


def _build_daily_kpi_statuses(
    *,
    funnel_type: str = "landing",
    spend: float,
    link_click: int,
    landing_page_view: int,
    ghl_total: int,
    meta_leads: int,
    ctr: float = 0.0,
    booked_leads: int = 0,
    showed_leads: int = 0,
    closed_leads: int = 0,
) -> dict[str, str]:
    meta_cpl = round(spend / meta_leads, 2) if meta_leads else 0.0
    ghl_lead_cost = round(spend / ghl_total, 2) if ghl_total else 0.0
    click_to_landing = safe_pct(landing_page_view, link_click)
    landing_to_lead = safe_pct(ghl_total, landing_page_view)
    cpc = round(spend / link_click, 2) if link_click else 0.0
    landing_status = "N/A" if funnel_type == "webinar" else _status_from_ranges(click_to_landing, ok_min=70, watch_min=60)
    landing_to_lead_status = "N/A" if funnel_type == "webinar" else _status_from_ranges(landing_to_lead, ok_min=5, watch_min=3)

    return {
        "meta_spend": _status_from_thresholds(spend, ok_min=1000, watch_min=1),
        "link_click": _link_click_status(cpc=cpc, ctr=ctr),
        "landing_page_view": landing_status,
        "ghl_lead": _status_from_thresholds(ghl_total, ok_min=3, watch_min=1),
        "meta_cpl": _inverse_status_from_thresholds(meta_cpl, ok_max=3000, watch_max=5000, zero_is_bad=True),
        "ghl_lead_cost": _inverse_status_from_thresholds(ghl_lead_cost, ok_max=3000, watch_max=5000, zero_is_bad=True),
        "click_to_landing": landing_status,
        "landing_to_lead": landing_to_lead_status,
        "lead_to_booking": _funnel_ratio_status(booked_leads, ghl_total, ok_min=30, watch_min=10),
        "booking_to_show": _funnel_ratio_status(showed_leads, booked_leads, ok_min=70, watch_min=50),
        "show_to_close": _funnel_ratio_status(closed_leads, showed_leads, ok_min=20, watch_min=10),
    }


def _status_from_thresholds(value: float, *, ok_min: float, watch_min: float) -> str:
    if value >= ok_min:
        return "OK"
    if value >= watch_min:
        return "FIGYELNI"
    return "BEAVATKOZÁS"


def _status_from_ranges(value: float, *, ok_min: float, watch_min: float) -> str:
    if value >= ok_min:
        return "OK"
    if value >= watch_min:
        return "FIGYELNI"
    return "BEAVATKOZÁS"


def _inverse_status_from_thresholds(
    value: float,
    *,
    ok_max: float,
    watch_max: float,
    zero_is_bad: bool = False,
) -> str:
    if zero_is_bad and value <= 0:
        return "BEAVATKOZÁS"
    if value <= ok_max:
        return "OK"
    if value <= watch_max:
        return "FIGYELNI"
    return "BEAVATKOZÁS"


def _funnel_ratio_status(numerator: int, denominator: int, *, ok_min: float, watch_min: float) -> str:
    if denominator <= 0:
        return "FIGYELNI"
    return _status_from_ranges(safe_pct(numerator, denominator), ok_min=ok_min, watch_min=watch_min)


def _link_click_status(*, cpc: float, ctr: float) -> str:
    primary = _inverse_status_from_thresholds(cpc, ok_max=120, watch_max=180, zero_is_bad=True)
    secondary = _inverse_status_from_thresholds(max(ctr, 0.0), ok_max=-1, watch_max=-1)
    if ctr >= 2.0:
        secondary = "OK"
    elif ctr >= 1.2:
        secondary = "FIGYELNI"
    else:
        secondary = "BEAVATKOZÁS"

    if primary == "OK" and secondary == "BEAVATKOZÁS":
        return "FIGYELNI"
    return primary


def _evaluate_meta_adset(
    *,
    funnel_type: str = "landing",
    spend: float,
    link_click: int,
    landing_page_views: int,
    registration_leads: int,
) -> str:
    if funnel_type == "webinar":
        if spend > 0 and link_click == 0 and registration_leads == 0:
            return "Webinár: van költés, de nincs mérhető interakció vagy űrlap lead."
        if registration_leads > 0:
            return "Webinár: működik, hoz Meta űrlap leadet."
        return "Webinár: landing kontroll nem releváns; Meta űrlap lead alapján értékelendő."
    if spend > 0 and link_click == 0:
        return "Hirdetés gyenge: van költés, nincs link click."
    if link_click > 0 and landing_page_views == 0:
        return "Tracking vagy landing hiba: van link click, nincs landing view."
    if link_click > 0 and safe_pct(landing_page_views, link_click) < 40:
        return "Landing mérés/oldal gyenge: sok click elveszik landing előtt."
    if landing_page_views > 0 and registration_leads == 0:
        return "Landing/offer gyenge: van landing forgalom, nincs Meta lead."
    if registration_leads > 0:
        return "Működik: hoz Meta leadet."
    return "Nincs elég adat a döntéshez."


def build_ga4_landing_comparison(
    landing_rows: list[dict[str, Any]],
    current_period_start: date,
    current_label: str,
    previous_label: str,
) -> dict[str, Any]:
    previous_rows = [row for row in landing_rows if parse_row_date(row) < current_period_start]
    current_rows = [row for row in landing_rows if parse_row_date(row) >= current_period_start]

    current_summary = _summarize_ga4_landing_rows(current_rows)
    previous_summary = _summarize_ga4_landing_rows(previous_rows)

    cards = []
    for metric, label in (
        ("landing_users", "LANDING OLDALRA ÉRKEZETT"),
        ("booking_users", "ELJUTOTT A FOGLALÁS OLDALRA"),
        ("thank_you_users", "ELJUTOTT A KÖSZÖNJÜK OLDALRA"),
    ):
        current_value = current_summary[metric]
        previous_value = previous_summary[metric]
        delta = current_value - previous_value
        direction = "flat"
        if delta > 0:
            direction = "up"
        elif delta < 0:
            direction = "down"
        cards.append(
            {
                "metric": metric,
                "title": label,
                "value": current_value,
                "pct_change": compute_pct_change(current_value, previous_value),
                "direction": direction,
            }
        )

    return {
        "current_period": {
            "label": current_label,
            "summary": current_summary,
        },
        "previous_period": {
            "label": previous_label,
            "summary": previous_summary,
        },
        "cards": cards,
    }


def summarize_rows_only(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = Counter()
    meeting_counts: list[int] = []

    for row in rows:
        for field in FUNNEL_FIELDS:
            totals[field] += row[field]
        meeting_counts.extend(row.get("_meeting_counts", []))

    return _build_summary_from_totals(totals=totals, meeting_counts=meeting_counts)


def _summarize_ga4_landing_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    landing_users = sum(int(row.get("landing_users", 0)) for row in rows)
    booking_users = sum(int(row.get("booking_users", 0)) for row in rows)
    thank_you_users = sum(int(row.get("thank_you_users", 0)) for row in rows)
    return {
        "landing_users": landing_users,
        "booking_users": booking_users,
        "thank_you_users": thank_you_users,
        "landing_to_booking_pct": safe_pct(booking_users, landing_users),
        "landing_to_thank_you_pct": safe_pct(thank_you_users, landing_users),
    }


def build_comparison_quick_snapshot(
    *,
    current_summary: dict[str, Any],
    meta_summary: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    meta_summary = meta_summary or {}
    spend = float(meta_summary.get("spend", 0.0))
    link_click = int(meta_summary.get("link_click", 0))
    landing_page_view = int(meta_summary.get("landing_page_views", 0))
    meta_leads = int(meta_summary.get("registration_leads", 0))
    ghl_total = int(current_summary.get("new_leads", 0))
    ctr = round(float(meta_summary.get("ctr", 0.0)), 2)

    statuses = _build_daily_kpi_statuses(
        spend=spend,
        link_click=link_click,
        landing_page_view=landing_page_view,
        ghl_total=ghl_total,
        meta_leads=meta_leads,
        ctr=ctr,
    )

    snapshot_rows = [
        ("Meta Spend", _format_plain_number(round(spend, 2)), statuses["meta_spend"]),
        ("Link Click", _format_plain_number(link_click), statuses["link_click"]),
        ("Landing Page View", _format_plain_number(landing_page_view), statuses["landing_page_view"]),
        ("GHL Lead", _format_plain_number(ghl_total), statuses["ghl_lead"]),
        ("Meta CPL", _format_plain_number(round(spend / meta_leads, 2) if meta_leads else 0.0), statuses["meta_cpl"]),
        ("GHL lead költség", _format_plain_number(round(spend / ghl_total, 2) if ghl_total else 0.0), statuses["ghl_lead_cost"]),
        ("Click → landing", f"{safe_pct(landing_page_view, link_click)}%", statuses["click_to_landing"]),
        ("Landing → lead", f"{safe_pct(ghl_total, landing_page_view)}%", statuses["landing_to_lead"]),
    ]
    return [
        {
            "label": label,
            "value": value,
            "status": status,
        }
        for label, value, status in snapshot_rows
    ]


def _landing_card_title(url: str) -> str:
    if "lioncare.hu/landing-meta-nyugdij" in url:
        return "LIONCARE LANDINGRŐL JÖTT LEAD"
    if "finshield.hu/kata-nyugdij" in url:
        return "FINSHIELD LANDINGRŐL JÖTT LEAD"
    host_and_path = url.replace("https://", "").replace("http://", "")
    return f"LANDING LEAD: {host_and_path}"


def _build_ghl_landing_rows(
    *,
    contacts: list[dict[str, Any]],
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for contact in contacts:
        lead_date = _contact_lead_date(contact)
        if not isinstance(lead_date, date) or not (start_date <= lead_date <= end_date):
            continue
        landing_url = contact.get("landing_page_url") or "ismeretlen"
        counts[str(landing_url)] += 1
    return [{"landing_url": landing_url, "lead_count": count} for landing_url, count in counts.most_common()]


def _difference_pct(source_value: int, comparison_value: int) -> float:
    if source_value == 0 and comparison_value == 0:
        return 0.0
    denominator = max(source_value, comparison_value, 1)
    return round(((source_value - comparison_value) / denominator) * 100, 2)


def write_csv_report(csv_path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
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
    ]

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})


def write_weekly_comparison_csv(csv_path: Path, comparison: dict[str, Any]) -> None:
    fieldnames = ["metric", "current", "previous", "delta", "pct_change", "direction"]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in comparison["comparison_rows"]:
            writer.writerow(row)


def write_html_report(
    html_path: Path,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    start_date: date,
    end_date: date,
    ga4_data: dict[str, Any] | None = None,
    landing_lead_cards: list[dict[str, Any]] | None = None,
    meta_data: dict[str, Any] | None = None,
    decision_report: dict[str, Any] | None = None,
) -> None:
    env = _build_template_env()
    template = env.get_template("report.html.j2")
    rendered = template.render(
        title="Napi funnel riport",
        rows=rows,
        summary=summary,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        ga4_data=ga4_data,
        landing_lead_cards=landing_lead_cards or [],
        meta_data=meta_data,
        decision_report=decision_report,
    )
    html_path.write_text(rendered, encoding="utf-8")


def write_weekly_comparison_html(
    html_path: Path,
    comparison: dict[str, Any],
    start_date: date,
    end_date: date,
    title: str = "Weekly Funnel Comparison Report",
    user_meeting_comparison: dict[str, Any] | None = None,
    meta_data: dict[str, Any] | None = None,
) -> None:
    env = _build_template_env()
    template = env.get_template("comparison_report.html.j2")
    rendered = template.render(
        title=title,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        comparison=comparison,
        user_meeting_comparison=user_meeting_comparison,
        meta_data=meta_data,
    )
    html_path.write_text(rendered, encoding="utf-8")


def write_comparison_pdf(
    pdf_path: Path,
    comparison: dict[str, Any],
    title: str,
    start_date: date,
    end_date: date,
    user_meeting_comparison: dict[str, Any] | None = None,
    overview_description: str = "Az időszak funnel teljesítményének fő lépései és konverziói",
) -> None:
    _register_pdf_fonts()
    styles = getSampleStyleSheet()
    styles["Title"].textColor = BRAND_NAVY
    styles["Title"].fontName = PDF_FONT_BOLD_NAME
    styles["Heading2"].textColor = BRAND_NAVY
    styles["Heading2"].fontSize = 18
    styles["Heading2"].spaceAfter = 2 * mm
    styles["Heading2"].fontName = PDF_FONT_BOLD_NAME
    styles["Normal"].textColor = BRAND_TEXT
    styles["Normal"].fontSize = 10.5
    styles["Normal"].leading = 13
    styles["Normal"].fontName = PDF_FONT_NAME
    story = [
        *_build_pdf_cover(title=title, start_date=start_date, end_date=end_date),
        Spacer(1, 6 * mm),
    ]

    story.extend(_build_primary_card_table(comparison.get("primary_cards", [])))

    story.extend(
        [
            Spacer(1, 8 * mm),
            Paragraph("Funnel áttekintés", styles["Heading2"]),
            Paragraph(overview_description, styles["Normal"]),
            Spacer(1, 3 * mm),
            *_build_funnel_strip(comparison["current_period"]["summary"]),
        ]
    )

    if user_meeting_comparison and user_meeting_comparison["rows"]:
        story.extend(
            [
                Spacer(1, 8 * mm),
                Paragraph("Tanácsadói teljesítmény", styles["Heading2"]),
                Paragraph(
                    f"Előző időszak: {user_meeting_comparison['previous_label']} | Aktuális időszak: {user_meeting_comparison['current_label']}",
                    styles["Normal"],
                ),
                Spacer(1, 3 * mm),
            ]
        )
        story.extend(_build_user_card_table(user_meeting_comparison.get("cards", [])))

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        rightMargin=8 * mm,
        leftMargin=8 * mm,
        topMargin=8 * mm,
        bottomMargin=8 * mm,
    )
    doc.build(story, onFirstPage=_draw_pdf_background, onLaterPages=_draw_pdf_background)


def safe_pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100, 2)


def compute_pct_change(current_value: float, previous_value: float) -> Any:
    if previous_value == 0:
        return None
    return round(((current_value - previous_value) / previous_value) * 100, 2)


def parse_row_date(row: dict[str, Any]) -> date:
    return date.fromisoformat(str(row["date"]))


def build_primary_cards(comparison_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted_metrics = [
        ("new_leads", "Új leadek"),
        ("booked_leads", "Foglalások"),
        ("showed_leads", "Megjelentek"),
        ("closed_leads", "Szerződések"),
        ("avg_meetings_per_closed", "Átlag meeting / szerződés"),
    ]
    lookup = {row["metric"]: row for row in comparison_rows}
    return [
        {
            "metric": metric,
            "title": label.upper(),
            "value": lookup[metric]["current"],
            "pct_change": lookup[metric]["pct_change"],
            "direction": lookup[metric]["direction"],
        }
        for metric, label in wanted_metrics
    ]


def build_user_cards(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "user_label": row["user_label"],
            "booked_value": row["current_booked"],
            "booked_pct_change": row["booked_pct_change"],
            "booked_direction": row["booked_direction"],
            "showed_value": row["current_showed"],
            "showed_pct_change": row["showed_pct_change"],
            "showed_direction": row["showed_direction"],
            "no_show_value": row["current_no_show"],
            "cancelled_value": row["current_cancelled"],
            "current_first_meeting_showed": row["current_first_meeting_showed"],
            "current_second_meeting_showed": row["current_second_meeting_showed"],
            "current_thirdplus_meeting_showed": row["current_thirdplus_meeting_showed"],
        }
        for row in rows
    ]


def build_user_meeting_comparison(
    appointments: list[dict[str, Any]],
    previous_start: date,
    current_start: date,
    current_end: date,
    ordinal_appointments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    user_labels = _load_user_labels()
    grouped: dict[str, dict[str, int]] = {}

    def default_stats(assigned_user_id: str) -> dict[str, int]:
        return {
            "user_id": assigned_user_id,
            "user_label": user_labels.get(assigned_user_id, assigned_user_id),
            "previous_booked": 0,
            "previous_showed": 0,
            "previous_no_show": 0,
            "previous_cancelled": 0,
            "current_booked": 0,
            "current_showed": 0,
            "current_no_show": 0,
            "current_cancelled": 0,
            "current_first_meeting_showed": 0,
            "current_second_meeting_showed": 0,
            "current_thirdplus_meeting_showed": 0,
        }

    for appointment in appointments:
        assigned_user_id = str(appointment.get("assignedUserId") or appointment.get("assignedTo") or "").strip()
        if not assigned_user_id:
            assigned_user_id = "unassigned"

        appointment_date = _extract_appointment_date(appointment)
        if appointment_date is None:
            continue
        if not (previous_start <= appointment_date <= current_end):
            continue

        stats = grouped.setdefault(assigned_user_id, default_stats(assigned_user_id))
        period_prefix = "current" if appointment_date >= current_start else "previous"
        stats[f"{period_prefix}_booked"] += 1

        status = str(
            appointment.get("appointmentStatus")
            or appointment.get("status")
            or appointment.get("calendarStatus")
            or appointment.get("appoinmentStatus")
            or ""
        ).strip().lower()
        if status in SHOWED_STATUSES:
            stats[f"{period_prefix}_showed"] += 1
        elif appointment.get("deleted") or status in CANCELLED_STATUSES:
            stats[f"{period_prefix}_cancelled"] += 1
        elif status in NO_SHOW_STATUSES:
            stats[f"{period_prefix}_no_show"] += 1

    appointments_by_contact: dict[str, list[dict[str, Any]]] = {}
    for appointment in (ordinal_appointments or appointments):
        if appointment.get("deleted"):
            continue
        contact_id = _extract_appointment_contact_id(appointment)
        if not contact_id:
            continue
        appointment_date = _extract_appointment_date(appointment)
        if appointment_date is None or appointment_date > current_end:
            continue
        appointments_by_contact.setdefault(contact_id, []).append(appointment)

    for contact_appointments in appointments_by_contact.values():
        contact_appointments.sort(
            key=lambda item: (
                _extract_appointment_date(item) or date.min,
                str(item.get("id") or item.get("_id") or ""),
            )
        )
        showed_ordinal = 0

        for appointment in contact_appointments:
            appointment_date = _extract_appointment_date(appointment)
            if appointment_date is None:
                continue

            status = str(
                appointment.get("appointmentStatus")
                or appointment.get("status")
                or appointment.get("calendarStatus")
                or appointment.get("appoinmentStatus")
                or ""
            ).strip().lower()
            if status not in SHOWED_STATUSES:
                continue

            showed_ordinal += 1
            if not (current_start <= appointment_date <= current_end):
                continue

            assigned_user_id = str(appointment.get("assignedUserId") or appointment.get("assignedTo") or "").strip()
            if not assigned_user_id:
                assigned_user_id = "unassigned"

            stats = grouped.setdefault(assigned_user_id, default_stats(assigned_user_id))

            if showed_ordinal == 1:
                stats["current_first_meeting_showed"] += 1
            elif showed_ordinal == 2:
                stats["current_second_meeting_showed"] += 1
            else:
                stats["current_thirdplus_meeting_showed"] += 1

    rows = []
    for stats in grouped.values():
        current_booked = stats["current_booked"]
        previous_booked = stats["previous_booked"]
        current_showed = stats["current_showed"]
        previous_showed = stats["previous_showed"]
        row = dict(stats)
        row["current_show_rate_pct"] = safe_pct(current_showed, current_booked)
        row["previous_show_rate_pct"] = safe_pct(previous_showed, previous_booked)
        row["booked_delta"] = current_booked - previous_booked
        row["showed_delta"] = current_showed - previous_showed
        row["show_rate_delta"] = round(row["current_show_rate_pct"] - row["previous_show_rate_pct"], 2)
        row["booked_pct_change"] = compute_pct_change(current_booked, previous_booked)
        row["showed_pct_change"] = compute_pct_change(current_showed, previous_showed)
        row["show_rate_pct_change"] = compute_pct_change(row["current_show_rate_pct"], row["previous_show_rate_pct"])
        row["booked_direction"] = _direction_from_delta(row["booked_delta"])
        row["showed_direction"] = _direction_from_delta(row["showed_delta"])
        row["show_rate_direction"] = _direction_from_delta(row["show_rate_delta"])
        rows.append(row)

    rows.sort(key=lambda item: (-item["current_showed"], -item["current_booked"], item["user_label"]))
    comparison = {
        "rows": rows,
        "previous_label": f"{previous_start.isoformat()} to {(current_start - timedelta(days=1)).isoformat()}",
        "current_label": f"{current_start.isoformat()} to {current_end.isoformat()}",
    }
    comparison["cards"] = build_user_cards(rows)
    return comparison


def _extract_appointment_date(appointment: dict[str, Any]) -> date | None:
    for key in ("startTime", "dateAdded", "endTime"):
        raw = appointment.get(key)
        if not raw:
            continue
        raw_text = str(raw).strip().replace("Z", "+00:00")
        candidates = [raw_text, raw_text.replace(" ", "T")]
        for candidate in candidates:
            try:
                return date.fromisoformat(candidate[:10])
            except ValueError:
                continue
    return None


def _extract_appointment_contact_id(appointment: dict[str, Any]) -> str:
    candidates = [
        appointment.get("contactId"),
        appointment.get("contact_id"),
        appointment.get("appointmentContactId"),
        appointment.get("contact", {}).get("id") if isinstance(appointment.get("contact"), dict) else None,
    ]
    for candidate in candidates:
        if candidate:
            return str(candidate).strip()
    return ""


def _load_user_labels() -> dict[str, str]:
    raw = os.getenv("GHL_USER_LABELS", "").strip()
    mapping: dict[str, str] = {}
    if not raw:
        return mapping

    for pair in raw.split(","):
        if ":" not in pair:
            continue
        user_id, label = pair.split(":", 1)
        if user_id.strip() and label.strip():
            mapping[user_id.strip()] = _owner_display_label(label.strip())
    return mapping


def _direction_from_delta(delta: float) -> str:
    if delta > 0:
        return "up"
    if delta < 0:
        return "down"
    return "flat"


def _trend_symbol(direction: str) -> str:
    if direction == "up":
        return "↑"
    if direction == "down":
        return "↓"
    return "→"


def _styled_pdf_table(rows: list[list[str]], kind: str = "default") -> Table:
    if kind == "comparison":
        col_widths = [62 * mm, 24 * mm, 24 * mm, 22 * mm, 28 * mm, 16 * mm]
        header_font_size = 9
        body_font_size = 8.8
    elif kind == "user_detail":
        col_widths = [34 * mm, 16 * mm, 16 * mm, 20 * mm, 18 * mm, 14 * mm, 14 * mm, 15 * mm, 18 * mm, 22 * mm]
        header_font_size = 7.8
        body_font_size = 8.2
    else:
        col_widths = None
        header_font_size = 9
        body_font_size = 8.5

    table = Table(rows, repeatRows=1, colWidths=col_widths)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3ecde")),
                ("TEXTCOLOR", (0, 0), (-1, 0), BRAND_NAVY),
                ("TEXTCOLOR", (0, 1), (-1, -1), BRAND_TEXT),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor("#d3c4a6")),
                ("LINEBELOW", (0, 1), (-1, -1), 0.35, colors.HexColor("#e6dfd1")),
                ("FONTNAME", (0, 0), (-1, 0), PDF_FONT_BOLD_NAME),
                ("FONTNAME", (0, 1), (-1, -1), PDF_FONT_NAME),
                ("FONTSIZE", (0, 0), (-1, 0), header_font_size),
                ("FONTSIZE", (0, 1), (-1, -1), body_font_size),
                ("LEADING", (0, 0), (-1, 0), header_font_size + 2),
                ("LEADING", (0, 1), (-1, -1), body_font_size + 2),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fbf8f2")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("ALIGN", (0, 0), (0, -1), "LEFT"),
                ("ALIGN", (1, 0), (-2, -1), "RIGHT"),
                ("ALIGN", (-1, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )

    if kind == "comparison":
        table.setStyle(
            TableStyle(
                [
                    ("TEXTCOLOR", (4, 1), (4, -1), BRAND_NAVY),
                    ("FONTNAME", (4, 1), (4, -1), PDF_FONT_BOLD_NAME),
                    ("FONTNAME", (5, 1), (5, -1), PDF_FONT_BOLD_NAME),
                ]
            )
        )
    elif kind == "user_detail":
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (2, 1), (4, -1), PDF_FONT_BOLD_NAME),
                    ("FONTNAME", (8, 1), (9, -1), PDF_FONT_BOLD_NAME),
                ]
            )
        )
    return table


def _build_pdf_cover(title: str, start_date: date, end_date: date) -> list[Any]:
    logo_path = Path(__file__).resolve().parent / "2lion.png"
    return [
        PdfHeaderFlowable(
            logo_path=logo_path if logo_path.exists() else None,
            title=title,
            subtitle=f"Időszak: {start_date.isoformat()} - {end_date.isoformat()}",
        )
    ]


def _build_primary_card_table(cards: list[dict[str, Any]]) -> list[Any]:
    if not cards:
        return []
    flow_cards = []
    for card in cards:
        trend_text = "n/a" if card["pct_change"] is None else f"{card['pct_change']}%"
        flow_cards.append(
            {
                "title": card["title"],
                "value": _format_metric_value(str(card["metric"]), card["value"]),
                "trend": f"{_trend_symbol(card['direction'])} {_format_pct_change(card['pct_change'])}",
                "trend_color": _trend_color(card["direction"]),
            }
        )
    return [PdfCardGridFlowable(cards=flow_cards, columns=3, card_height=30 * mm)]


def _build_user_card_table(cards: list[dict[str, Any]]) -> list[Any]:
    if not cards:
        return []
    flow_cards = []
    for card in cards:
        booked_trend = "n/a" if card["booked_pct_change"] is None else f"{card['booked_pct_change']}%"
        showed_trend = "n/a" if card["showed_pct_change"] is None else f"{card['showed_pct_change']}%"
        flow_cards.append(
            {
                "title": card["user_label"],
                "sections": [
                    {
                        "label": "FOGLALÁSOK",
                        "value": _format_plain_number(card["booked_value"]),
                        "trend": f"{_trend_symbol(card['booked_direction'])} {_format_pct_change(card['booked_pct_change'])}",
                        "trend_color": _trend_color(card["booked_direction"]),
                    },
                    {
                        "label": "MEGJELENTEK",
                        "value": _format_plain_number(card["showed_value"]),
                        "trend": f"{_trend_symbol(card['showed_direction'])} {_format_pct_change(card['showed_pct_change'])}",
                        "trend_color": _trend_color(card["showed_direction"]),
                    },
                    {
                        "label": "MEGOSZLÁS",
                        "lines": [
                            f"1. alkalom: {card['current_first_meeting_showed']}",
                            f"2. alkalom: {card['current_second_meeting_showed']}",
                            f"3. alkalom: {card['current_thirdplus_meeting_showed']}",
                            f"Nem jelent meg: {card['no_show_value']}",
                            f"Törölt / lemondott: {card['cancelled_value']}",
                        ],
                    },
                ],
            }
        )
    return [PdfAdvisorGridFlowable(cards=flow_cards)]


def _draw_pdf_background(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFillColor(colors.white)
    canvas.rect(0, 0, A4[0], A4[1], stroke=0, fill=1)
    canvas.restoreState()


def _trend_color(direction: str) -> str:
    if direction == "up":
        return "#22C55E"
    if direction == "down":
        return "#EF4444"
    return "#94A3B8"


def _format_plain_number(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _format_percent(value: Any) -> str:
    if value is None:
        return "n/a"
    numeric = float(value)
    if numeric.is_integer():
        return f"{int(numeric)}%"
    return f"{numeric:.2f}".rstrip("0").rstrip(".") + "%"


def _format_pct_change(value: Any) -> str:
    if value is None:
        return "n/a"
    numeric = float(value)
    sign = "+" if numeric > 0 else ""
    if numeric.is_integer():
        return f"{sign}{int(numeric)}%"
    formatted = f"{numeric:.2f}".rstrip("0").rstrip(".")
    return f"{sign}{formatted}%"


def _format_trend_with_pct(direction: str, pct_value: Any) -> str:
    return f"{_trend_symbol(direction)} {_format_pct_change(pct_value)}"


def _format_metric_value(metric: str, value: Any) -> str:
    if metric.endswith("_pct"):
        return _format_percent(value)
    return _format_plain_number(value)


def _format_metric_delta(metric: str, value: Any) -> str:
    if metric.endswith("_pct"):
        numeric = float(value)
        sign = "+" if numeric > 0 else ""
        if numeric.is_integer():
            return f"{sign}{int(numeric)} pp"
        formatted = f"{numeric:.2f}".rstrip("0").rstrip(".")
        return f"{sign}{formatted} pp"
    numeric = float(value)
    sign = "+" if numeric > 0 else ""
    if numeric.is_integer():
        return f"{sign}{int(numeric)}"
    return f"{sign}{numeric:.2f}".rstrip("0").rstrip(".")


def _pdf_colored_paragraph(text: str, color: str, alignment: int = 2) -> Paragraph:
    style = ParagraphStyle(
        name=f"inline-{alignment}-{color}",
        fontName=PDF_FONT_BOLD_NAME,
        fontSize=8.5,
        leading=10.5,
        textColor=colors.HexColor(color),
        alignment=alignment,
    )
    return Paragraph(text, style)


def _register_pdf_fonts() -> None:
    font_path = next((path for path in PDF_FONT_CANDIDATES if os.path.exists(path)), None)
    if font_path is None:
        raise FileNotFoundError(
            "No usable TrueType font found for PDF generation. "
            f"Checked: {', '.join(PDF_FONT_CANDIDATES)}"
        )
    if PDF_FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(PDF_FONT_NAME, font_path))
    if PDF_FONT_BOLD_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(PDF_FONT_BOLD_NAME, font_path))


def _build_funnel_strip(summary: dict[str, Any]) -> list[Any]:
    header_style = ParagraphStyle(
        name="funnel-header",
        fontName=PDF_FONT_BOLD_NAME,
        fontSize=9,
        leading=11,
        textColor=BRAND_MUTED,
        alignment=1,
    )
    value_style = ParagraphStyle(
        name="funnel-value",
        fontName=PDF_FONT_BOLD_NAME,
        fontSize=24,
        leading=26,
        textColor=BRAND_NAVY,
        alignment=1,
    )
    rate_style = ParagraphStyle(
        name="funnel-rate",
        fontName=PDF_FONT_BOLD_NAME,
        fontSize=11,
        leading=13,
        textColor=BRAND_GOLD,
        alignment=1,
    )
    arrow_style = ParagraphStyle(
        name="funnel-arrow",
        fontName=PDF_FONT_BOLD_NAME,
        fontSize=20,
        leading=22,
        textColor=BRAND_GOLD,
        alignment=1,
    )

    steps = [
        ("Lead", _format_plain_number(summary["new_leads"]), ""),
        ("Foglalás", _format_plain_number(summary["booked_leads"]), _format_percent(summary["lead_to_booking_pct"])),
        ("Megjelent", _format_plain_number(summary["showed_leads"]), _format_percent(summary["booking_to_show_pct"])),
        ("Szerződés", _format_plain_number(summary["closed_leads"]), _format_percent(summary["show_to_close_pct"])),
    ]

    cells: list[Any] = []
    for index, (label, value, rate) in enumerate(steps):
        step_table = Table(
            [[Paragraph(label, header_style)], [Paragraph(value, value_style)], [Paragraph(rate or "&nbsp;", rate_style)]],
            colWidths=[28 * mm],
        )
        step_table.setStyle(
            TableStyle(
                [
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        cells.append(step_table)
        if index < len(steps) - 1:
            cells.append(Paragraph("→", arrow_style))

    funnel_table = Table([cells], colWidths=[28 * mm, 8 * mm, 28 * mm, 8 * mm, 28 * mm, 8 * mm, 28 * mm])
    funnel_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f9fbfd")),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#d9e0e8")),
                ("ROUNDEDCORNERS", [14, 14, 14, 14]),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]
        )
    )
    return [funnel_table]


class PdfHeaderFlowable(Flowable):
    def __init__(self, logo_path: Path | None, title: str, subtitle: str) -> None:
        super().__init__()
        self.logo_path = logo_path
        self.title = title
        self.subtitle = subtitle
        self.height = 36 * mm

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:
        self.width = availWidth
        return availWidth, self.height

    def draw(self) -> None:
        canvas = self.canv
        canvas.saveState()
        canvas.setFillColor(colors.white)
        canvas.rect(0, 0, self.width, self.height, stroke=0, fill=1)

        padding = 12
        logo_size = self.height - (padding * 2)
        text_start_x = padding
        if self.logo_path:
            canvas.drawImage(
                str(self.logo_path),
                padding,
                padding,
                width=logo_size,
                height=logo_size,
                preserveAspectRatio=True,
                mask="auto",
            )
            text_start_x = padding + logo_size + 14

        title_y = self.height - 18
        canvas.setFillColor(BRAND_NAVY)
        canvas.setFont(PDF_FONT_BOLD_NAME, 20)
        canvas.drawString(text_start_x, title_y, self.title)
        canvas.setFillColor(BRAND_MUTED)
        canvas.setFont(PDF_FONT_NAME, 10.5)
        canvas.drawString(text_start_x, title_y - 16, self.subtitle)
        canvas.setStrokeColor(colors.HexColor("#d9e0e8"))
        canvas.setLineWidth(0.8)
        canvas.line(0, 0, self.width, 0)
        canvas.restoreState()


class PdfCardGridFlowable(Flowable):
    def __init__(self, cards: list[dict[str, Any]], columns: int, card_height: float) -> None:
        super().__init__()
        self.cards = cards
        self.columns = columns
        self.card_height = card_height
        self.gap = 10
        self.radius = 12
        self.padding_x = 14
        self.padding_y = 12

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:
        self.width = availWidth
        self.card_width = (availWidth - (self.gap * (self.columns - 1))) / self.columns
        row_count = max(1, math.ceil(len(self.cards) / self.columns))
        self.height = row_count * self.card_height + (row_count - 1) * self.gap
        return availWidth, self.height

    def draw(self) -> None:
        canvas = self.canv
        row_count = max(1, math.ceil(len(self.cards) / self.columns))

        for index, card in enumerate(self.cards):
            row = index // self.columns
            col = index % self.columns
            x = col * (self.card_width + self.gap)
            y = self.height - ((row + 1) * self.card_height) - (row * self.gap)
            self._draw_card(canvas, x, y, self.card_width, self.card_height, card)

    def _draw_card(self, canvas, x: float, y: float, width: float, height: float, card: dict[str, Any]) -> None:
        canvas.saveState()
        canvas.setFillColor(BRAND_NAVY_SOFT)
        canvas.setStrokeColor(BRAND_GOLD)
        canvas.setLineWidth(1.1)
        canvas.roundRect(x, y, width, height, self.radius, stroke=1, fill=1)

        cursor_y = y + height - self.padding_y

        if "sections" in card:
            canvas.setFillColor(BRAND_GOLD)
            canvas.setFont(PDF_FONT_BOLD_NAME, 10 if self.columns == 2 else 9)
            canvas.drawString(x + self.padding_x, cursor_y, str(card["title"]))
            cursor_y -= 18
            cursor_y -= 2
            for section in card["sections"]:
                canvas.setFillColor(BRAND_CREAM)
                canvas.setFont(PDF_FONT_BOLD_NAME, 7.8)
                canvas.drawString(x + self.padding_x, cursor_y, str(section["label"]))
                cursor_y -= 13

                if section["label"] == "MEGOSZLÁS":
                    canvas.setFillColor(BRAND_GOLD)
                    canvas.setFont(PDF_FONT_BOLD_NAME, 10)
                    for line in section.get("lines", []):
                        canvas.drawString(x + self.padding_x, cursor_y, str(line))
                        cursor_y -= 12
                    cursor_y -= 4
                else:
                    value = str(section["value"])
                    canvas.setFillColor(BRAND_GOLD)
                    canvas.setFont(PDF_FONT_BOLD_NAME, 20)
                    canvas.drawString(x + self.padding_x, cursor_y, value)
                    cursor_y -= 17

                    trend = section.get("trend")
                    if trend:
                        canvas.setFillColor(colors.HexColor(section.get("trend_color", "#D9D9D9")))
                        canvas.setFont(PDF_FONT_BOLD_NAME, 10)
                        canvas.drawString(x + self.padding_x, cursor_y, trend)
                        cursor_y -= 18
                    else:
                        cursor_y -= 6
        else:
            title_font_size = 9
            value_font_size = 30
            trend_font_size = 11

            canvas.setFillColor(BRAND_GOLD)
            canvas.setFont(PDF_FONT_BOLD_NAME, title_font_size)
            canvas.drawCentredString(x + (width / 2), y + height - 14, str(card["title"]))

            canvas.setFont(PDF_FONT_BOLD_NAME, value_font_size)
            canvas.drawCentredString(x + (width / 2), y + (height / 2) + 2, str(card["value"]))

            canvas.setFillColor(colors.HexColor(card.get("trend_color", "#D9D9D9")))
            canvas.setFont(PDF_FONT_BOLD_NAME, trend_font_size)
            canvas.drawCentredString(x + (width / 2), y + 14, str(card["trend"]))

        canvas.restoreState()


class PdfAdvisorGridFlowable(Flowable):
    def __init__(self, cards: list[dict[str, Any]]) -> None:
        super().__init__()
        self.cards = cards
        self.gap = 10
        self.card_height = 58 * mm
        self.radius = 12

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:
        self.width = availWidth
        self.card_width = (availWidth - self.gap) / 2
        self.height = self.card_height
        return availWidth, self.height

    def draw(self) -> None:
        canvas = self.canv
        for index, card in enumerate(self.cards[:2]):
            x = index * (self.card_width + self.gap)
            self._draw_advisor_card(canvas, x, 0, self.card_width, self.card_height, card)

    def _draw_advisor_card(self, canvas, x: float, y: float, width: float, height: float, card: dict[str, Any]) -> None:
        canvas.saveState()
        canvas.setFillColor(BRAND_NAVY_SOFT)
        canvas.setStrokeColor(BRAND_GOLD)
        canvas.setLineWidth(1.0)
        canvas.roundRect(x, y, width, height, self.radius, stroke=1, fill=1)

        inner_left = x + 12
        inner_top = y + height - 14
        column_gap = 18
        column_width = (width - 24 - column_gap) / 2
        left_x = inner_left
        right_x = inner_left + column_width + column_gap
        top_y = inner_top

        canvas.setFillColor(BRAND_GOLD)
        canvas.setFont(PDF_FONT_BOLD_NAME, 13)
        canvas.drawString(left_x, top_y, str(card["title"]))

        sections = card.get("sections", [])
        left_sections = [section for section in sections if section["label"] != "MEGOSZLÁS"]
        order_section = next((section for section in sections if section["label"] == "MEGOSZLÁS"), None)

        cursor_y = top_y - 24
        for section in left_sections:
            block_height = 58
            canvas.setFillColor(colors.Color(1, 1, 1, alpha=0.05))
            canvas.setStrokeColor(colors.Color(1, 1, 1, alpha=0.10))
            canvas.roundRect(left_x, cursor_y - block_height + 8, column_width, block_height, 8, stroke=1, fill=1)

            label_y = cursor_y
            value_y = cursor_y - 25
            trend_y = cursor_y - 41

            canvas.setFillColor(BRAND_CREAM)
            canvas.setFont(PDF_FONT_BOLD_NAME, 7.4)
            canvas.drawString(left_x + 8, label_y, str(section["label"]))

            canvas.setFillColor(BRAND_GOLD)
            canvas.setFont(PDF_FONT_BOLD_NAME, 18)
            canvas.drawString(left_x + 8, value_y, str(section["value"]))

            canvas.setFillColor(colors.HexColor(section.get("trend_color", "#94A3B8")))
            canvas.setFont(PDF_FONT_BOLD_NAME, 9)
            canvas.drawString(left_x + 8, trend_y, str(section.get("trend", "")))
            cursor_y -= 66

        if order_section:
            panel_height = 100
            panel_top = top_y - 22
            canvas.setFillColor(colors.Color(1, 1, 1, alpha=0.05))
            canvas.setStrokeColor(colors.Color(1, 1, 1, alpha=0.10))
            canvas.roundRect(right_x, panel_top - panel_height + 8, column_width, panel_height, 8, stroke=1, fill=1)

            canvas.setFillColor(BRAND_CREAM)
            canvas.setFont(PDF_FONT_BOLD_NAME, 7.4)
            canvas.drawString(right_x + 8, panel_top, "MEGOSZLÁS")

            order_y = panel_top - 12
            canvas.setFillColor(BRAND_GOLD)
            canvas.setFont(PDF_FONT_BOLD_NAME, 9.4)
            for line in order_section.get("lines", []):
                canvas.drawString(right_x + 8, order_y, str(line))
                order_y -= 14

        canvas.restoreState()


def _empty_daily_row(day: date) -> dict[str, Any]:
    return {
        "date": day.isoformat(),
        "new_leads": 0,
        "booked_leads": 0,
        "showed_leads": 0,
        "closed_leads": 0,
        "lead_to_booking_pct": 0.0,
        "booking_to_show_pct": 0.0,
        "show_to_close_pct": 0.0,
        "avg_meetings_per_closed": 0.0,
        "median_meetings_per_closed": 0.0,
        "closed_1_meeting": 0,
        "closed_2_meetings": 0,
        "closed_3plus_meetings": 0,
        "_meeting_counts": [],
    }


def _enrich_row_with_derived_metrics(row: dict[str, Any], meeting_counts: list[int]) -> None:
    row["_meeting_counts"] = list(meeting_counts)
    row["lead_to_booking_pct"] = safe_pct(row["booked_leads"], row["new_leads"])
    row["booking_to_show_pct"] = safe_pct(row["showed_leads"], row["booked_leads"])
    row["show_to_close_pct"] = safe_pct(row["closed_leads"], row["showed_leads"])
    row["avg_meetings_per_closed"] = round(sum(meeting_counts) / len(meeting_counts), 2) if meeting_counts else 0.0
    row["median_meetings_per_closed"] = round(statistics.median(meeting_counts), 2) if meeting_counts else 0.0
    row["closed_1_meeting"] = sum(1 for count in meeting_counts if count == 1)
    row["closed_2_meetings"] = sum(1 for count in meeting_counts if count == 2)
    row["closed_3plus_meetings"] = sum(1 for count in meeting_counts if count >= 3)


def _build_summary_from_totals(totals: Counter, meeting_counts: list[int]) -> dict[str, Any]:
    return {
        "new_leads": totals["new_leads"],
        "booked_leads": totals["booked_leads"],
        "showed_leads": totals["showed_leads"],
        "closed_leads": totals["closed_leads"],
        "lead_to_booking_pct": safe_pct(totals["booked_leads"], totals["new_leads"]),
        "booking_to_show_pct": safe_pct(totals["showed_leads"], totals["booked_leads"]),
        "show_to_close_pct": safe_pct(totals["closed_leads"], totals["showed_leads"]),
        "avg_meetings_per_closed": round(sum(meeting_counts) / len(meeting_counts), 2) if meeting_counts else 0.0,
        "median_meetings_per_closed": round(statistics.median(meeting_counts), 2) if meeting_counts else 0.0,
        "closed_1_meeting": sum(1 for count in meeting_counts if count == 1),
        "closed_2_meetings": sum(1 for count in meeting_counts if count == 2),
        "closed_3plus_meetings": sum(1 for count in meeting_counts if count >= 3),
    }


def _build_template_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(Path(__file__).resolve().parent / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )
