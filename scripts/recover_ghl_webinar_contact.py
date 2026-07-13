from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghl_client import GHLAPIError, GHLClient, GHLConfig


def normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def normalize_phone(value: str | None) -> str:
    return re.sub(r"\D+", "", value or "")


def contact_value(contact: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = contact.get(key)
        if value:
            return str(value)
    return ""


def find_existing_contact(client: GHLClient, email: str, phone: str) -> dict[str, Any] | None:
    target_email = normalize_email(email)
    target_phone = normalize_phone(phone)

    for contact in client.iter_contacts():
        candidate_email = normalize_email(contact_value(contact, "email"))
        candidate_phone = normalize_phone(contact_value(contact, "phone"))
        if target_email and candidate_email == target_email:
            return contact
        if target_phone and candidate_phone == target_phone:
            return contact

    return None


def update_lead_date(client: GHLClient, contact_id: str, lead_date: str) -> bool:
    if not lead_date:
        return False

    try:
        client.update_contact_custom_fields(contact_id, {"lead_date": lead_date})
    except (GHLAPIError, ValueError) as exc:
        print(f"WARNING: contact recovered but lead_date custom field update failed: {exc}")
        return False

    return True


def recover_contact(args: argparse.Namespace) -> dict[str, Any]:
    client = GHLClient(GHLConfig.from_env())
    existing = find_existing_contact(client, args.email, args.phone)

    if existing:
        contact_id = contact_value(existing, "id", "contactId")
        lead_date_updated = update_lead_date(client, contact_id, args.lead_date)
        return {
            "action": "found_existing",
            "contact_id": contact_id,
            "email": args.email,
            "phone": args.phone,
            "lead_date_updated": lead_date_updated,
        }

    payload: dict[str, Any] = {
        "locationId": client.config.location_id,
        "firstName": args.first_name,
        "lastName": args.last_name,
        "name": f"{args.first_name} {args.last_name}".strip(),
        "email": args.email,
        "phone": args.phone,
        "source": args.source,
    }
    if args.tags:
        payload["tags"] = [tag.strip() for tag in args.tags.split(",") if tag.strip()]

    response = client._request("POST", "/contacts/upsert", json=payload)
    data = response.json()
    contact = data.get("contact") or data.get("data") or data
    contact_id = contact_value(contact, "id", "contactId")

    if not contact_id:
        raise RuntimeError(f"GHL did not return a contact id. Response: {json.dumps(data, ensure_ascii=False)}")

    lead_date_updated = update_lead_date(client, contact_id, args.lead_date)
    return {
        "action": "created_or_upserted",
        "contact_id": contact_id,
        "email": args.email,
        "phone": args.phone,
        "lead_date_updated": lead_date_updated,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recover a missing Meta webinar lead into GHL.")
    parser.add_argument("--first-name", required=True)
    parser.add_argument("--last-name", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--phone", required=True)
    parser.add_argument("--lead-date", required=True)
    parser.add_argument("--source", default="Meta Lead Ads - Webinar manual recovery")
    parser.add_argument("--tags", default="")
    return parser.parse_args()


def main() -> None:
    result = recover_contact(parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
