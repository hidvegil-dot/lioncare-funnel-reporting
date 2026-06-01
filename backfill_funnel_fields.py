import argparse
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

from dotenv import load_dotenv

from ghl_client import GHLClient, GHLConfig


BACKFILL_START_DATE = date(2026, 3, 15)
SHOWED_STATUSES = {
    "showed",
    "show",
    "completed",
    "confirmed-show",
    "attended",
    "attended_meeting",
}
CLOSED_OPPORTUNITY_STATUSES = {
    "won",
    "closed",
    "closed_won",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill missing GHL funnel custom fields from contacts, appointments, and opportunities."
    )
    parser.add_argument(
        "--since",
        default=BACKFILL_START_DATE.isoformat(),
        help="Only inspect contacts created on or after this date (YYYY-MM-DD). Default: 2026-03-15.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually write missing custom fields back to GHL. Default is dry-run.",
    )
    return parser.parse_args()


def parse_iso_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def parse_datetime_value(raw_value: Any) -> Optional[datetime]:
    if raw_value in (None, ""):
        return None
    if isinstance(raw_value, datetime):
        return raw_value if raw_value.tzinfo else raw_value.replace(tzinfo=timezone.utc)
    if isinstance(raw_value, date):
        return datetime.combine(raw_value, datetime.min.time(), tzinfo=timezone.utc)
    if isinstance(raw_value, (int, float)):
        timestamp = raw_value / 1000 if raw_value > 10_000_000_000 else raw_value
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)

    raw_text = str(raw_value).strip()
    candidates = [
        raw_text,
        raw_text.replace("Z", "+00:00"),
        raw_text.replace(" ", "T"),
    ]
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw_text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def as_custom_field_date(value: Union[datetime, date]) -> str:
    if isinstance(value, datetime):
        current = value.astimezone(timezone.utc).date()
    else:
        current = value
    return f"{current.isoformat()}T00:00:00.000Z"


def contact_created_on_or_after(contact: dict[str, Any], since: date) -> bool:
    created_at = parse_datetime_value(contact.get("dateAdded"))
    return bool(created_at and created_at.date() >= since)


def pick_first_appointment_datetime(appointments: list[dict[str, Any]]) -> Optional[datetime]:
    candidates = []
    for appointment in appointments:
        if appointment.get("deleted"):
            continue
        for key in ("startTime", "dateAdded", "endTime"):
            parsed = parse_datetime_value(appointment.get(key))
            if parsed:
                candidates.append(parsed)
                break
    return min(candidates) if candidates else None


def pick_first_showed_datetime(appointments: list[dict[str, Any]]) -> Optional[datetime]:
    candidates = []
    for appointment in appointments:
        if appointment.get("deleted"):
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
        for key in ("startTime", "dateAdded", "endTime"):
            parsed = parse_datetime_value(appointment.get(key))
            if parsed:
                candidates.append(parsed)
                break
    return min(candidates) if candidates else None


def pick_close_datetime(client: GHLClient, raw_contact: dict[str, Any]) -> Optional[datetime]:
    candidates = []
    for opportunity in raw_contact.get("opportunities") or []:
        if not isinstance(opportunity, dict):
            continue
        status = str(opportunity.get("status") or "").strip().lower()
        if status not in CLOSED_OPPORTUNITY_STATUSES:
            continue

        opportunity_id = opportunity.get("id")
        if not opportunity_id:
            continue

        detailed = client.fetch_opportunity(str(opportunity_id))
        for key in ("lastStatusChangeAt", "lastStageChangeAt", "updatedAt", "createdAt", "dateAdded"):
            parsed = parse_datetime_value(detailed.get(key))
            if parsed:
                candidates.append(parsed)
                break
    return min(candidates) if candidates else None


def derive_lead_status(resolved_values: dict[str, Any]) -> str:
    if resolved_values.get("close_date"):
        return "closed"
    if resolved_values.get("show_date"):
        return "showed"
    if resolved_values.get("first_booking_date"):
        return "booked"
    return "new"


def main() -> None:
    load_dotenv(dotenv_path=Path(".env"))
    args = parse_args()
    since = parse_iso_date(args.since)

    client = GHLClient(GHLConfig.from_env())
    field_map = client.get_custom_field_map()

    inspected_contacts = 0
    counters = Counter()

    for raw_contact in client.iter_contacts():
        if not contact_created_on_or_after(raw_contact, since):
            continue

        inspected_contacts += 1
        normalized = client.normalize_contact(raw_contact, field_map)
        appointments = client.fetch_contact_appointments(normalized["id"])

        derived_updates: dict[str, Any] = {}
        created_at = parse_datetime_value(raw_contact.get("dateAdded"))

        if not normalized.get("lead_date") and created_at:
            derived_updates["lead_date"] = as_custom_field_date(created_at)

        if not normalized.get("first_booking_date"):
            first_booking_at = pick_first_appointment_datetime(appointments)
            if first_booking_at:
                derived_updates["first_booking_date"] = as_custom_field_date(first_booking_at)

        if not normalized.get("show_date"):
            showed_at = pick_first_showed_datetime(appointments)
            if showed_at:
                derived_updates["show_date"] = as_custom_field_date(showed_at)

        if not normalized.get("close_date"):
            close_at = pick_close_datetime(client, raw_contact)
            if close_at:
                derived_updates["close_date"] = as_custom_field_date(close_at)

        resolved_status_inputs = {
            "lead_date": normalized.get("lead_date") or derived_updates.get("lead_date"),
            "first_booking_date": normalized.get("first_booking_date") or derived_updates.get("first_booking_date"),
            "show_date": normalized.get("show_date") or derived_updates.get("show_date"),
            "close_date": normalized.get("close_date") or derived_updates.get("close_date"),
        }
        if not normalized.get("lead_status"):
            derived_updates["lead_status"] = derive_lead_status(resolved_status_inputs)

        if not derived_updates:
            continue

        print(
            f"[{'WRITE' if args.write else 'DRY-RUN'}] contact={normalized['id']} "
            f"name={normalized.get('name') or '-'} updates={derived_updates}"
        )

        for field_name in derived_updates:
            counters[field_name] += 1

        if args.write:
            client.update_contact_custom_fields(normalized["id"], derived_updates)

    print("")
    print("Backfill summary")
    print(f"inspected_contacts: {inspected_contacts}")
    print(f"lead_date_backfilled: {counters['lead_date']}")
    print(f"first_booking_date_backfilled: {counters['first_booking_date']}")
    print(f"show_date_backfilled: {counters['show_date']}")
    print(f"close_date_backfilled: {counters['close_date']}")
    print(f"lead_status_updated: {counters['lead_status']}")
    print(f"mode: {'write' if args.write else 'dry-run'}")


if __name__ == "__main__":
    main()
