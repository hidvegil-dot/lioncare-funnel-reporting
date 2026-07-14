from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghl_client import GHLAPIError, GHLClient, GHLConfig


WEBINAR_PATTERNS = ("webinar", "webinár", "webinar4")
EMAIL_PATTERNS = ("email", "mail", "e-mail", "levél", "kiküldés", "follow-up")
DATE_PATTERNS = (
    "2027.07.20",
    "2027-07-20",
    "2027/07/20",
    "2026.07.20",
    "2026-07-20",
    "2026/07/20",
    "07.20",
    "07-20",
    "július 20",
    "julius 20",
    "07.21",
    "07-21",
    "2026-07-21",
    "július 21",
    "julius 21",
    "18:00",
    "20:00",
)


def contact_value(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value:
            return str(value)
    return ""


def compact_text(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return re.sub(r"\s+", " ", text)


def contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in patterns)


def list_workflows(client: GHLClient) -> list[dict[str, Any]]:
    response = client._request("GET", "/workflows/", params={"locationId": client.config.location_id})
    payload = response.json()
    workflows = payload.get("workflows") or payload.get("data") or payload.get("results") or []
    return workflows if isinstance(workflows, list) else []


def fetch_workflow_detail(client: GHLClient, workflow_id: str) -> dict[str, Any]:
    attempts = [
        ("GET", f"/workflows/{workflow_id}", {"locationId": client.config.location_id}),
        ("GET", f"/workflows/{workflow_id}", None),
        ("GET", f"/locations/{client.config.location_id}/workflows/{workflow_id}", None),
    ]
    errors = []
    for method, path, params in attempts:
        try:
            response = client._request(method, path, params=params) if params else client._request(method, path)
            data = response.json()
            return {
                "ok": True,
                "endpoint": path,
                "data": data,
            }
        except GHLAPIError as exc:
            errors.append({"endpoint": path, "status_code": exc.status_code, "error": str(exc)})
    return {"ok": False, "errors": errors}


def snippets_for_patterns(value: Any, patterns: tuple[str, ...]) -> list[str]:
    text = compact_text(value)
    lowered = text.lower()
    snippets = []
    for pattern in patterns:
        idx = lowered.find(pattern.lower())
        if idx < 0:
            continue
        start = max(0, idx - 180)
        end = min(len(text), idx + len(pattern) + 240)
        snippet = text[start:end]
        if snippet not in snippets:
            snippets.append(snippet)
    return snippets


def inspect() -> dict[str, Any]:
    client = GHLClient(GHLConfig.from_env())
    workflows = list_workflows(client)
    rows = []

    for workflow in workflows:
        workflow_id = contact_value(workflow, "id", "_id", "workflowId")
        name = contact_value(workflow, "name", "title")
        status = contact_value(workflow, "status", "state")
        raw_text = compact_text(workflow)
        is_relevant_name = contains_any(name, WEBINAR_PATTERNS) or (
            contains_any(name, EMAIL_PATTERNS) and contains_any(name, ("kata", "nyugdíj", "nyugdij", "lioncare"))
        )
        is_relevant_payload = contains_any(raw_text, WEBINAR_PATTERNS)
        if not (is_relevant_name or is_relevant_payload):
            continue

        detail = fetch_workflow_detail(client, workflow_id) if workflow_id else {"ok": False, "errors": ["missing id"]}
        detail_data = detail.get("data") if detail.get("ok") else {}
        combined = {"list_item": workflow, "detail": detail_data}
        combined_text = compact_text(combined)
        rows.append(
            {
                "id": workflow_id,
                "name": name,
                "status": status,
                "detail_endpoint": detail.get("endpoint"),
                "detail_available": bool(detail.get("ok")),
                "contains_email_terms": contains_any(combined_text, EMAIL_PATTERNS),
                "contains_webinar_terms": contains_any(combined_text, WEBINAR_PATTERNS),
                "contains_2027_07_20": contains_any(combined_text, ("2027.07.20", "2027-07-20", "2027/07/20")),
                "contains_2026_07_20": contains_any(combined_text, ("2026.07.20", "2026-07-20", "2026/07/20", "július 20", "julius 20", "07.20", "07-20")),
                "contains_2026_07_21": contains_any(combined_text, ("2026.07.21", "2026-07-21", "2026/07/21", "július 21", "julius 21", "07.21", "07-21")),
                "contains_18_00": "18:00" in combined_text,
                "contains_20_00": "20:00" in combined_text,
                "date_snippets": snippets_for_patterns(combined, DATE_PATTERNS),
                "detail_errors": detail.get("errors", []),
            }
        )

    return {
        "checked_workflows": len(workflows),
        "relevant_workflows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect GHL workflow email/date content for webinar-related workflows.")
    parser.parse_args()
    print(json.dumps(inspect(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
