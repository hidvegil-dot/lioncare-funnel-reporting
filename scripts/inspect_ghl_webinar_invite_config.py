from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghl_client import GHLAPIError, GHLClient, GHLConfig


MATCH_PATTERNS = (
    "webinar",
    "webinár",
    "webinar4",
    "meghív",
    "meghivo",
    "időpont",
    "idopont",
    "dátum",
    "datum",
    "július",
    "julius",
    "07.21",
    "07. 21",
    "07-21",
    "2026-07-21",
    "21",
)


def normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def matches(value: Any) -> bool:
    text = compact_json(value).lower()
    return any(pattern in text for pattern in MATCH_PATTERNS)


def get_custom_values(client: GHLClient) -> list[dict[str, Any]]:
    response = client._request("GET", f"/locations/{client.config.location_id}/customValues")
    payload = response.json()
    values = payload.get("customValues") or payload.get("data") or payload.get("values") or []
    return values if isinstance(values, list) else []


def get_custom_fields(client: GHLClient) -> list[dict[str, Any]]:
    response = client._request("GET", f"/locations/{client.config.location_id}/customFields")
    payload = response.json()
    fields = payload.get("customFields") or payload.get("fields") or payload.get("data") or []
    return fields if isinstance(fields, list) else []


def contact_value(contact: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = contact.get(key)
        if value:
            return str(value)
    return ""


def normalize_email(value: Any) -> str:
    return normalize(value)


def normalize_phone(value: Any) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def find_contacts(client: GHLClient, leads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contacts = client.iter_contacts()
    by_email = {normalize_email(contact_value(contact, "email")): contact for contact in contacts if contact_value(contact, "email")}
    by_phone = {normalize_phone(contact_value(contact, "phone")): contact for contact in contacts if contact_value(contact, "phone")}
    results = []
    for lead in leads:
        contact = by_email.get(normalize_email(lead.get("email"))) or by_phone.get(normalize_phone(lead.get("phone")))
        if not contact:
            results.append({"lead": lead, "found": False})
            continue
        results.append(
            {
                "lead": lead,
                "found": True,
                "contact_id": contact_value(contact, "id", "contactId"),
                "name": contact_value(contact, "name"),
                "email": contact_value(contact, "email"),
                "phone": contact_value(contact, "phone"),
                "source": contact_value(contact, "source"),
                "tags": contact.get("tags") or [],
                "customFields": contact.get("customFields") or contact.get("custom_fields") or [],
            }
        )
    return results


def inspect(leads: list[dict[str, Any]]) -> dict[str, Any]:
    client = GHLClient(GHLConfig.from_env())
    custom_values = get_custom_values(client)
    custom_fields = get_custom_fields(client)
    return {
        "matching_custom_values": [value for value in custom_values if matches(value)],
        "matching_custom_fields": [field for field in custom_fields if matches(field)],
        "contacts": find_contacts(client, leads),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect GHL webinar invite date configuration.")
    parser.add_argument("--leads-json", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    leads = json.loads(args.leads_json)
    try:
        result = inspect(leads)
    except GHLAPIError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
