from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghl_client import GHLAPIError, GHLClient, GHLConfig


DEFAULT_WORKFLOW_SELECTORS = ("webinar", "webinár", "level", "levél", "email", "mail")


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


def split_name(full_name: str) -> tuple[str, str]:
    parts = [part.strip() for part in str(full_name or "").split() if part.strip()]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def lead_date_from_created_time(created_time: str) -> str:
    raw = str(created_time or "").strip()
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw).date().isoformat()
    except ValueError:
        return raw[:10]


def find_existing_contacts(client: GHLClient) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_email: dict[str, dict[str, Any]] = {}
    by_phone: dict[str, dict[str, Any]] = {}

    for contact in client.iter_contacts():
        email = normalize_email(contact_value(contact, "email"))
        phone = normalize_phone(contact_value(contact, "phone"))
        if email:
            by_email[email] = contact
        if phone:
            by_phone[phone] = contact

    return by_email, by_phone


def find_existing_contact(
    lead: dict[str, Any],
    by_email: dict[str, dict[str, Any]],
    by_phone: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    email = normalize_email(lead.get("email"))
    phone = normalize_phone(lead.get("phone"))
    return by_email.get(email) or by_phone.get(phone)


def upsert_contact(client: GHLClient, lead: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    name = str(lead.get("name") or "").strip()
    first_name, last_name = split_name(name)
    phone = str(lead.get("phone") or "").strip().replace("p:", "")
    email = str(lead.get("email") or "").strip()
    lead_date = lead_date_from_created_time(str(lead.get("created_time") or ""))

    payload: dict[str, Any] = {
        "locationId": client.config.location_id,
        "firstName": first_name,
        "lastName": last_name,
        "name": name,
        "email": email,
        "phone": phone,
        "source": "Meta Lead Ads - Webinar manual recovery",
        "tags": ["webinar", "meta-lead", "manual-recovery", "webinar4"],
    }

    response = client._request("POST", "/contacts/upsert", json=payload)
    data = response.json()
    contact = data.get("contact") or data.get("data") or data
    contact_id = contact_value(contact, "id", "contactId") or contact_value(existing or {}, "id", "contactId")
    if not contact_id:
        raise RuntimeError(f"GHL did not return a contact id for {email}. Response: {json.dumps(data, ensure_ascii=False)}")

    field_update_ok = True
    field_update_error = ""
    try:
        client.update_contact_custom_fields(
            contact_id,
            {
                "lead_date": lead_date,
                "lead_status": str(lead.get("lead_status") or "complete"),
            },
        )
    except (GHLAPIError, ValueError) as exc:
        field_update_ok = False
        field_update_error = str(exc)

    return {
        "meta_lead_id": lead.get("id"),
        "name": name,
        "email": email,
        "phone": phone,
        "lead_date": lead_date,
        "contact_id": contact_id,
        "contact_action": "updated_existing" if existing else "created_or_upserted",
        "field_update_ok": field_update_ok,
        "field_update_error": field_update_error,
    }


def list_workflows(client: GHLClient) -> list[dict[str, Any]]:
    payload = None
    last_error: Exception | None = None
    for params in ({"locationId": client.config.location_id}, None):
        try:
            response = client._request("GET", "/workflows/", params=params) if params else client._request("GET", "/workflows/")
            payload = response.json()
            break
        except GHLAPIError as exc:
            last_error = exc
            if exc.status_code not in {400, 422}:
                raise

    if payload is None:
        raise RuntimeError(f"Could not list GHL workflows: {last_error}")

    workflows = payload.get("workflows") or payload.get("data") or payload.get("results") or []
    if isinstance(workflows, dict):
        workflows = workflows.get("workflows") or workflows.get("data") or []
    if not isinstance(workflows, list):
        return []
    return [workflow for workflow in workflows if isinstance(workflow, dict)]


def workflow_id(workflow: dict[str, Any]) -> str:
    return contact_value(workflow, "id", "_id", "workflowId")


def workflow_name(workflow: dict[str, Any]) -> str:
    return contact_value(workflow, "name", "title")


def select_workflow(client: GHLClient, workflow_id_arg: str, workflow_name_contains: str) -> tuple[str, list[dict[str, str]]]:
    workflows = list_workflows(client)
    compact = [
        {
            "id": workflow_id(workflow),
            "name": workflow_name(workflow),
            "status": contact_value(workflow, "status", "state"),
        }
        for workflow in workflows
    ]

    if workflow_id_arg:
        return workflow_id_arg, compact

    selector = workflow_name_contains.strip().lower()
    selectors = [selector] if selector else list(DEFAULT_WORKFLOW_SELECTORS)
    matches = [
        workflow
        for workflow in workflows
        if workflow_id(workflow)
        and any(item in workflow_name(workflow).lower() for item in selectors)
    ]

    if len(matches) == 1:
        return workflow_id(matches[0]), compact

    raise RuntimeError(
        "Could not uniquely select a GHL workflow. "
        f"Matches={len(matches)}. Provide workflow_id or a narrower workflow_name_contains. "
        f"Available workflows: {json.dumps(compact, ensure_ascii=False)}"
    )


def add_contact_to_workflow(client: GHLClient, contact_id: str, workflow_id_value: str) -> dict[str, Any]:
    try:
        response = client._request("POST", f"/contacts/{contact_id}/workflow/{workflow_id_value}")
    except GHLAPIError as exc:
        text = str(exc)
        already = exc.status_code in {400, 422} and "already" in text.lower()
        return {
            "workflow_added": already,
            "workflow_status": "already_in_workflow" if already else "failed",
            "workflow_error": "" if already else text,
        }

    return {
        "workflow_added": True,
        "workflow_status": "added",
        "workflow_response": response.json() if response.text.strip() else {},
    }


def recover(args: argparse.Namespace) -> dict[str, Any]:
    leads = json.loads(args.leads_json)
    if not isinstance(leads, list):
        raise ValueError("leads_json must be a JSON array")

    client = GHLClient(GHLConfig.from_env())
    selected_workflow_id, available_workflows = select_workflow(
        client,
        workflow_id_arg=args.workflow_id.strip(),
        workflow_name_contains=args.workflow_name_contains.strip(),
    )
    by_email, by_phone = find_existing_contacts(client)

    results = []
    for lead in leads:
        existing = find_existing_contact(lead, by_email, by_phone)
        contact_result = upsert_contact(client, lead, existing)
        workflow_result = add_contact_to_workflow(client, contact_result["contact_id"], selected_workflow_id)
        contact_result.update(workflow_result)
        results.append(contact_result)

    return {
        "workflow_id": selected_workflow_id,
        "available_workflows": available_workflows,
        "processed_contacts": len(results),
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recover Meta webinar leads into GHL and add them to a workflow.")
    parser.add_argument("--leads-json", required=True)
    parser.add_argument("--workflow-id", default="")
    parser.add_argument("--workflow-name-contains", default="")
    return parser.parse_args()


def main() -> None:
    result = recover(parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
