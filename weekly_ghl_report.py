from __future__ import annotations

import argparse
import csv
import logging
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from ghl_client import GHLClient, GHLConfig
from report_builder import safe_pct
from report_storage import persist_weekly_ai_analysis
from weekly_ai_summary import build_weekly_ai_summary
from weekly_report_generator import write_weekly_ghl_html, write_weekly_ghl_markdown


logger = logging.getLogger(__name__)


ADVISOR_LABELS = {
    "hidvegi_laszlo": "Hidvégi László",
    "gulyas_amelita": "Gulyás Amelita",
    "unassigned": "Nincs delegálva",
}

DEFAULT_GHL_USER_LABELS = {
    "xk8bttZvXTINd3NEnR91": "Hidvégi László",
    "0yqfaKsgtLWOdTKwf6uo": "Gulyás Amelita",
}


SHOWED_STATUSES = {"showed", "show", "completed", "confirmed-show", "attended", "attended_meeting"}
NO_SHOW_STATUSES = {"no-show", "noshow", "no_show", "did_not_show"}
CANCELLED_STATUSES = {"cancelled", "canceled", "cancel", "deleted"}
RESCHEDULED_STATUSES = {"rescheduled", "reschedule"}
WON_STATUSES = {"won", "closed", "closed-won", "closed_won"}
LOST_STATUSES = {"lost", "closed-lost", "closed_lost"}


@dataclass(frozen=True)
class WeekWindow:
    start: date
    end: date


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a weekly executive GHL funnel report.")
    parser.add_argument("--week-start", help="Monday week start in YYYY-MM-DD format. Defaults to last completed week.")
    parser.add_argument("--week-end", help="Week end in YYYY-MM-DD format. Defaults to week-start + 6 days.")
    parser.add_argument("--output-dir", default=".", help="Directory for generated weekly report files.")
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=output_dir / "weekly_report_run.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        force=True,
    )
    logging.getLogger().addHandler(logging.StreamHandler())

    window = resolve_week_window(args.week_start, args.week_end)
    client = GHLClient(GHLConfig.from_env())
    report = build_weekly_ghl_report(client=client, week=window)

    csv_path = output_dir / "weekly_ghl_funnel_report.csv"
    html_path = output_dir / "weekly_ghl_funnel_report.html"
    summary_path = output_dir / "weekly_ghl_ceo_summary.md"
    write_weekly_csv(csv_path, report)
    write_weekly_ghl_html(html_path=html_path, report=report)
    write_weekly_ghl_markdown(markdown_path=summary_path, report=report)
    persist_weekly_ai_analysis(
        week_start=window.start,
        week_end=window.end,
        html_path=html_path,
        summary_path=summary_path,
        csv_path=csv_path,
        output_dir=output_dir,
        report=report,
    )
    logger.info("Completed weekly GHL report week_start=%s week_end=%s", window.start, window.end)


def resolve_week_window(week_start_raw: str | None, week_end_raw: str | None) -> WeekWindow:
    if week_start_raw:
        start = datetime.strptime(week_start_raw, "%Y-%m-%d").date()
    else:
        today = date.today()
        current_monday = today - timedelta(days=today.weekday())
        start = current_monday - timedelta(days=7)
    end = datetime.strptime(week_end_raw, "%Y-%m-%d").date() if week_end_raw else start + timedelta(days=6)
    if end < start:
        raise ValueError("week-end must be later than or equal to week-start")
    return WeekWindow(start=start, end=end)


def build_weekly_ghl_report(*, client: GHLClient, week: WeekWindow) -> dict[str, Any]:
    previous = WeekWindow(start=week.start - timedelta(days=7), end=week.end - timedelta(days=7))
    contacts = client.fetch_all_contacts()
    current_contacts = _contacts_created_between(contacts, week)
    previous_contacts = _contacts_created_between(contacts, previous)
    appointments = client.fetch_appointments_for_contacts(contacts, start_date=previous.start, end_date=week.end)
    opportunities = _fetch_opportunities_safely(client)
    advisor_map = _advisor_map_from_env()

    current_metrics = _build_period_metrics(
        contacts=current_contacts,
        all_contacts=contacts,
        appointments=appointments,
        opportunities=opportunities,
        window=week,
        advisor_map=advisor_map,
    )
    previous_metrics = _build_period_metrics(
        contacts=previous_contacts,
        all_contacts=contacts,
        appointments=appointments,
        opportunities=opportunities,
        window=previous,
        advisor_map=advisor_map,
    )
    report = {
        "week_start": week.start.isoformat(),
        "week_end": week.end.isoformat(),
        "previous_week_start": previous.start.isoformat(),
        "previous_week_end": previous.end.isoformat(),
        "metrics": current_metrics,
        "previous_metrics": previous_metrics,
        "changes": _build_changes(current_metrics, previous_metrics),
    }
    report["diagnosis"] = build_weekly_ai_summary(report)
    return report


def _build_period_metrics(
    *,
    contacts: list[dict[str, Any]],
    all_contacts: list[dict[str, Any]],
    appointments: list[dict[str, Any]],
    opportunities: list[dict[str, Any]],
    window: WeekWindow,
    advisor_map: dict[str, str],
) -> dict[str, Any]:
    window_appointments = [item for item in appointments if _date_in_window(_appointment_date(item), window)]
    window_opportunities = [item for item in opportunities if _date_in_window(_opportunity_date(item), window)]
    advisors = _empty_advisor_rows()

    source_counts = Counter(_contact_source(contact) for contact in contacts)
    assigned_counts = Counter(_advisor_label(_contact_owner(contact), advisor_map) for contact in contacts)

    for contact in contacts:
        advisors[_advisor_key(_contact_owner(contact), advisor_map)]["new_leads"] += 1

    for appointment in window_appointments:
        contact = _find_contact_for_appointment(appointment, all_contacts)
        advisor_key = _advisor_key(_contact_owner(contact or {}), advisor_map)
        status = _appointment_status(appointment)
        advisors[advisor_key]["bookings"] += 1
        if status in SHOWED_STATUSES:
            advisors[advisor_key]["showed"] += 1
        elif status in NO_SHOW_STATUSES:
            advisors[advisor_key]["no_show"] += 1
        elif status in CANCELLED_STATUSES or status in RESCHEDULED_STATUSES:
            advisors[advisor_key]["cancelled"] += 1

    won_value = 0.0
    for opportunity in window_opportunities:
        contact = _find_contact_for_opportunity(opportunity, all_contacts)
        advisor_key = _advisor_key(_opportunity_owner(opportunity) or _contact_owner(contact or {}), advisor_map)
        status = _opportunity_status(opportunity)
        if status in WON_STATUSES:
            advisors[advisor_key]["won"] += 1
            won_value += _opportunity_value(opportunity)
        elif status in LOST_STATUSES:
            advisors[advisor_key]["lost"] += 1

    totals = {
        "new_leads": len(contacts),
        "bookings": len(window_appointments),
        "showed": sum(1 for item in window_appointments if _appointment_status(item) in SHOWED_STATUSES),
        "no_show": sum(1 for item in window_appointments if _appointment_status(item) in NO_SHOW_STATUSES),
        "cancelled": sum(
            1
            for item in window_appointments
            if _appointment_status(item) in CANCELLED_STATUSES or _appointment_status(item) in RESCHEDULED_STATUSES
        ),
        "won": sum(1 for item in window_opportunities if _opportunity_status(item) in WON_STATUSES),
        "lost": sum(1 for item in window_opportunities if _opportunity_status(item) in LOST_STATUSES),
        "won_value": round(won_value, 2),
    }
    totals.update(_rates(totals))
    for row in advisors.values():
        row.update(_rates(row))

    unknown_status_count = sum(1 for contact in contacts if not str(contact.get("lead_status") or "").strip())
    unassigned_count = advisors["unassigned"]["new_leads"]
    return {
        **totals,
        "source_breakdown": [{"source": key, "count": value} for key, value in source_counts.most_common()],
        "assigned_breakdown": [{"advisor": key, "count": value} for key, value in assigned_counts.most_common()],
        "advisor_rows": list(advisors.values()),
        "data_quality": {
            "unknown_status_count": unknown_status_count,
            "unassigned_leads": unassigned_count,
            "opportunities_available": bool(opportunities),
        },
    }


def _empty_advisor_rows() -> dict[str, dict[str, Any]]:
    return {
        key: {
            "advisor_key": key,
            "advisor": label,
            "new_leads": 0,
            "bookings": 0,
            "showed": 0,
            "no_show": 0,
            "cancelled": 0,
            "won": 0,
            "lost": 0,
        }
        for key, label in ADVISOR_LABELS.items()
    }


def _rates(row: dict[str, Any]) -> dict[str, float]:
    return {
        "lead_to_booking_rate": safe_pct(int(row.get("bookings", 0)), int(row.get("new_leads", 0))),
        "booking_to_show_rate": safe_pct(int(row.get("showed", 0)), int(row.get("bookings", 0))),
        "show_to_close_rate": safe_pct(int(row.get("won", 0)), int(row.get("showed", 0))),
    }


def _build_changes(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    changes = {}
    for key in ("new_leads", "bookings", "showed", "no_show", "cancelled", "won", "lost"):
        current_value = int(current.get(key, 0))
        previous_value = int(previous.get(key, 0))
        changes[key] = {
            "current": current_value,
            "previous": previous_value,
            "delta": current_value - previous_value,
            "pct_change": safe_pct(current_value - previous_value, previous_value),
        }
    return changes


def write_weekly_csv(csv_path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    fields = [
        "week_start",
        "week_end",
        "new_leads",
        "bookings",
        "showed",
        "no_show",
        "cancelled",
        "won",
        "lost",
        "lead_to_booking_rate",
        "booking_to_show_rate",
        "show_to_close_rate",
    ]
    row = {field: report.get(field, metrics.get(field, "")) for field in fields}
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow(row)


def _contacts_created_between(contacts: list[dict[str, Any]], window: WeekWindow) -> list[dict[str, Any]]:
    return [contact for contact in contacts if _date_in_window(contact.get("lead_date") or _contact_created_date(contact), window)]


def _date_in_window(value: date | None, window: WeekWindow) -> bool:
    return value is not None and window.start <= value <= window.end


def _contact_created_date(contact: dict[str, Any]) -> date | None:
    raw = (contact.get("raw") or {}).get("dateAdded") or (contact.get("raw") or {}).get("createdAt")
    return _parse_date(raw)


def _appointment_date(appointment: dict[str, Any]) -> date | None:
    return _parse_date(appointment.get("startTime") or appointment.get("dateAdded") or appointment.get("endTime"))


def _opportunity_date(opportunity: dict[str, Any]) -> date | None:
    return _parse_date(opportunity.get("lastStatusChangeAt") or opportunity.get("updatedAt") or opportunity.get("createdAt"))


def _parse_date(raw: Any) -> date | None:
    if not raw:
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    text = str(raw).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _appointment_status(appointment: dict[str, Any]) -> str:
    return str(
        appointment.get("appointmentStatus")
        or appointment.get("status")
        or appointment.get("calendarStatus")
        or ""
    ).strip().lower()


def _opportunity_status(opportunity: dict[str, Any]) -> str:
    return str(opportunity.get("status") or opportunity.get("pipelineStageName") or "").strip().lower()


def _opportunity_value(opportunity: dict[str, Any]) -> float:
    raw = opportunity.get("monetaryValue") or opportunity.get("value") or opportunity.get("amount") or 0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _contact_owner(contact: dict[str, Any]) -> str:
    raw = contact.get("raw") or {}
    return str(contact.get("assignedTo") or raw.get("assignedTo") or raw.get("userId") or raw.get("ownerId") or "").strip()


def _opportunity_owner(opportunity: dict[str, Any]) -> str:
    return str(opportunity.get("assignedTo") or opportunity.get("userId") or opportunity.get("ownerId") or "").strip()


def _advisor_map_from_env() -> dict[str, str]:
    mapping: dict[str, str] = dict(DEFAULT_GHL_USER_LABELS)
    for pair in os.getenv("GHL_USER_LABELS", "").split(","):
        if ":" not in pair:
            continue
        user_id, label = pair.split(":", 1)
        if user_id.strip() and label.strip():
            mapping[user_id.strip()] = label.strip()
    return mapping


def _advisor_label(owner_id: str, advisor_map: dict[str, str]) -> str:
    if not owner_id:
        return ADVISOR_LABELS["unassigned"]
    return advisor_map.get(owner_id, owner_id)


def _advisor_key(owner_id: str, advisor_map: dict[str, str]) -> str:
    label = _advisor_label(owner_id, advisor_map).lower()
    if "hidvégi" in label or "hidvegi" in label or "lászló" in label or "laszlo" in label:
        return "hidvegi_laszlo"
    if "gulyás" in label or "gulyas" in label or "amelita" in label:
        return "gulyas_amelita"
    return "unassigned" if not owner_id else "unassigned"


def _contact_source(contact: dict[str, Any]) -> str:
    return str(contact.get("landing_page_url") or contact.get("source") or "Ismeretlen").strip() or "Ismeretlen"


def _find_contact_for_appointment(appointment: dict[str, Any], contacts: list[dict[str, Any]]) -> dict[str, Any] | None:
    contact_id = str(appointment.get("contactId") or appointment.get("contact_id") or "").strip()
    return next((contact for contact in contacts if contact.get("id") == contact_id), None)


def _find_contact_for_opportunity(opportunity: dict[str, Any], contacts: list[dict[str, Any]]) -> dict[str, Any] | None:
    contact_id = str(opportunity.get("contactId") or opportunity.get("contact_id") or "").strip()
    return next((contact for contact in contacts if contact.get("id") == contact_id), None)


def _fetch_opportunities_safely(client: GHLClient) -> list[dict[str, Any]]:
    try:
        return client.fetch_opportunities()
    except Exception:
        logger.exception("Could not fetch GHL opportunities; weekly sales won/lost values will use zeroes")
        return []


if __name__ == "__main__":
    main()
