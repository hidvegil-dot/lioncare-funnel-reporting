from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghl_client import GHLAPIError, GHLClient, GHLConfig


def contact_value(contact: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = contact.get(key)
        if value:
            return str(value)
    return ""


def normalize_tags(contact: dict[str, Any]) -> set[str]:
    tags = contact.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    return {str(tag).strip().lower() for tag in tags if str(tag).strip()}


def parse_local_dt(value: str) -> datetime:
    raw = value.strip().replace("T", " ")
    if "+" in raw:
        raw = raw.split("+", 1)[0]
    return datetime.fromisoformat(raw[:19])


def same_local_dt(raw_value: Any, expected: str) -> bool:
    if not raw_value:
        return False
    try:
        return parse_local_dt(str(raw_value)) == parse_local_dt(expected)
    except ValueError:
        return str(raw_value).strip()[:19] == expected.strip()[:19]


def should_scan_contact(contact: dict[str, Any], required_tag: str) -> bool:
    if not required_tag:
        return True
    return required_tag.strip().lower() in normalize_tags(contact)


def update_appointment(
    client: GHLClient,
    appointment: dict[str, Any],
    *,
    new_start: str,
    new_end: str,
    dry_run: bool,
) -> dict[str, Any]:
    event_id = contact_value(appointment, "id", "eventId", "_id")
    if not event_id:
        return {"updated": False, "error": "appointment id missing"}

    payload: dict[str, Any] = {
        "calendarId": appointment.get("calendarId"),
        "contactId": appointment.get("contactId"),
        "startTime": new_start,
        "endTime": new_end,
        "title": appointment.get("title"),
        "appointmentStatus": appointment.get("appointmentStatus") or appointment.get("appoinmentStatus") or "confirmed",
        "assignedUserId": appointment.get("assignedUserId"),
        "address": appointment.get("address"),
        "ignoreDateRange": True,
        "ignoreFreeSlotValidation": True,
    }
    payload = {key: value for key, value in payload.items() if value not in (None, "")}

    if dry_run:
        return {"updated": False, "dry_run": True, "event_id": event_id, "payload": payload}

    try:
        response = client._request("PUT", f"/calendars/events/appointments/{event_id}", json=payload)
    except GHLAPIError:
        minimal_payload = {
            "startTime": new_start,
            "endTime": new_end,
            "appointmentStatus": payload.get("appointmentStatus", "confirmed"),
            "ignoreDateRange": True,
            "ignoreFreeSlotValidation": True,
        }
        response = client._request("PUT", f"/calendars/events/appointments/{event_id}", json=minimal_payload)
        payload = minimal_payload

    return {
        "updated": True,
        "event_id": event_id,
        "payload": payload,
        "response": response.json() if response.text.strip() else {},
    }


def fix_appointments(args: argparse.Namespace) -> dict[str, Any]:
    client = GHLClient(GHLConfig.from_env())
    results = []
    scanned_contacts = 0
    matched_appointments = 0

    for contact in client.iter_contacts():
        if not should_scan_contact(contact, args.required_tag):
            continue
        contact_id = contact_value(contact, "id", "contactId", "_id")
        if not contact_id:
            continue
        scanned_contacts += 1
        try:
            appointments = client.fetch_contact_appointments(contact_id)
        except GHLAPIError as exc:
            results.append({"contact_id": contact_id, "error": str(exc)})
            continue

        for appointment in appointments:
            if args.calendar_id and appointment.get("calendarId") != args.calendar_id:
                continue
            if not same_local_dt(appointment.get("startTime"), args.old_start):
                continue
            matched_appointments += 1
            update_result = update_appointment(
                client,
                appointment,
                new_start=args.new_start,
                new_end=args.new_end,
                dry_run=args.dry_run,
            )
            results.append(
                {
                    "contact_id": contact_id,
                    "email": contact.get("email"),
                    "name": contact.get("name") or " ".join(
                        part for part in [contact.get("firstName"), contact.get("lastName")] if part
                    ).strip(),
                    "old_start": appointment.get("startTime"),
                    "old_end": appointment.get("endTime"),
                    "new_start": args.new_start,
                    "new_end": args.new_end,
                    **update_result,
                }
            )

    return {
        "dry_run": args.dry_run,
        "required_tag": args.required_tag,
        "calendar_id": args.calendar_id,
        "old_start": args.old_start,
        "new_start": args.new_start,
        "scanned_contacts": scanned_contacts,
        "matched_appointments": matched_appointments,
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Move webinar appointments from one datetime to another.")
    parser.add_argument("--calendar-id", required=True)
    parser.add_argument("--required-tag", default="webinar_lead")
    parser.add_argument("--old-start", default="2026-07-21 18:00:00")
    parser.add_argument("--new-start", default="2026-07-20 18:00:00")
    parser.add_argument("--new-end", default="2026-07-20 19:00:00")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    print(json.dumps(fix_appointments(parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
