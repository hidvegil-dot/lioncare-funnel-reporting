from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghl_client import GHLClient, GHLConfig


def normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_phone(value: Any) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def contact_value(contact: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = contact.get(key)
        if value:
            return str(value)
    return ""


def normalize_name(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def index_contacts(
    contacts: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_email: dict[str, dict[str, Any]] = {}
    by_phone: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}

    for contact in contacts:
        email = normalize_email(contact_value(contact, "email"))
        phone = normalize_phone(contact_value(contact, "phone"))
        name = normalize_name(
            contact_value(contact, "name")
            or " ".join(
                part
                for part in [
                    contact_value(contact, "firstName"),
                    contact_value(contact, "lastName"),
                ]
                if part
            )
        )
        if email:
            by_email[email] = contact
        if phone:
            by_phone[phone] = contact
        if name:
            by_name[name] = contact

    return by_email, by_phone, by_name


def compact_attribution(value: Any) -> Any:
    if isinstance(value, list):
        return [compact_attribution(item) for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return value
    keys = (
        "url",
        "utmUrl",
        "utmSource",
        "utmMedium",
        "utmCampaign",
        "utmTerm",
        "utmContent",
        "source",
        "sessionSource",
        "medium",
        "mediumId",
        "referrer",
        "landingPage",
        "campaign",
    )
    return {key: value.get(key) for key in keys if value.get(key) not in (None, "", [], {})}


def contact_debug_summary(contact: dict[str, Any] | None) -> dict[str, Any]:
    if not contact:
        return {}
    return {
        "ghl_contact_id": contact_value(contact, "id", "contactId"),
        "ghl_name": contact_value(contact, "name")
        or " ".join(
            part
            for part in [
                contact_value(contact, "firstName"),
                contact_value(contact, "lastName"),
            ]
            if part
        ),
        "ghl_date_added": contact_value(contact, "dateAdded", "createdAt", "date_added"),
        "ghl_updated_at": contact_value(contact, "updatedAt", "dateUpdated"),
        "ghl_source": contact_value(contact, "source"),
        "ghl_campaign": contact_value(contact, "campaign"),
        "ghl_assigned_to": contact_value(contact, "assignedTo"),
        "ghl_tags": contact.get("tags") or [],
        "ghl_attribution_source": compact_attribution(contact.get("attributionSource")),
        "ghl_last_attribution_source": compact_attribution(contact.get("lastAttributionSource")),
        "ghl_attributions": compact_attribution(contact.get("attributions")),
    }


def check_leads(meta_leads: list[dict[str, Any]]) -> dict[str, Any]:
    client = GHLClient(GHLConfig.from_env())
    contacts = client.iter_contacts()
    by_email, by_phone, by_name = index_contacts(contacts)

    results = []
    for lead in meta_leads:
        email = normalize_email(lead.get("email"))
        phone = normalize_phone(lead.get("phone"))
        contact = by_email.get(email)
        matched_by = "email" if contact else None
        if contact is None and phone:
            contact = by_phone.get(phone)
            matched_by = "phone" if contact else None
        if contact is None and lead.get("name"):
            contact = by_name.get(normalize_name(lead.get("name")))
            matched_by = "name" if contact else None

        results.append(
            {
                "meta_lead_id": lead.get("id"),
                "created_time": lead.get("created_time"),
                "name": lead.get("name"),
                "email": lead.get("email"),
                "phone": lead.get("phone"),
                "lead_status": lead.get("lead_status"),
                "found_in_ghl": contact is not None,
                "matched_by": matched_by,
                **contact_debug_summary(contact),
            }
        )

    return {
        "checked_meta_leads": len(meta_leads),
        "ghl_contacts_scanned": len(contacts),
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only check whether Meta leads exist as GHL contacts.")
    parser.add_argument("--leads-json", required=True, help="JSON array with id, created_time, name, email, phone.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    meta_leads = json.loads(args.leads_json)
    result = check_leads(meta_leads)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
