from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghl_client import GHLClient, GHLConfig


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").strip().lower())
    return "".join(char for char in text if not unicodedata.combining(char))


def item_value(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def fetch_pipelines(client: GHLClient) -> list[dict[str, Any]]:
    response = client._request(
        "GET",
        "/opportunities/pipelines",
        params={"locationId": client.config.location_id},
    )
    payload = response.json()
    pipelines = payload.get("pipelines") or payload.get("data") or payload.get("results") or []
    return [pipeline for pipeline in pipelines if isinstance(pipeline, dict)]


def select_pipeline(pipelines: list[dict[str, Any]], pipeline_name: str) -> dict[str, Any]:
    target = normalize_text(pipeline_name)
    matches = [
        pipeline
        for pipeline in pipelines
        if normalize_text(item_value(pipeline, "name", "title")) == target
    ]
    if not matches:
        matches = [
            pipeline
            for pipeline in pipelines
            if target in normalize_text(item_value(pipeline, "name", "title"))
        ]
    if len(matches) != 1:
        raise SystemExit(
            json.dumps(
                {
                    "error": "pipeline match must be exactly one",
                    "target": pipeline_name,
                    "matches": [
                        {
                            "id": item_value(item, "id", "_id", "pipelineId"),
                            "name": item_value(item, "name", "title"),
                        }
                        for item in matches
                    ],
                    "available_pipelines": [
                        {
                            "id": item_value(item, "id", "_id", "pipelineId"),
                            "name": item_value(item, "name", "title"),
                        }
                        for item in pipelines
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return matches[0]


def opportunity_id(opportunity: dict[str, Any]) -> str:
    return item_value(opportunity, "id", "_id", "opportunityId")


def opportunity_pipeline_id(opportunity: dict[str, Any]) -> str:
    pipeline = opportunity.get("pipeline")
    if isinstance(pipeline, dict):
        nested = item_value(pipeline, "id", "_id", "pipelineId")
        if nested:
            return nested
    return item_value(opportunity, "pipelineId", "pipeline_id", "pipelineID")


def opportunity_status(opportunity: dict[str, Any]) -> str:
    return item_value(opportunity, "status").strip().lower()


def compact_opportunity(opportunity: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": opportunity_id(opportunity),
        "name": item_value(opportunity, "name", "title", "opportunityName"),
        "contact_id": item_value(opportunity, "contactId", "contact_id"),
        "status": opportunity_status(opportunity),
        "pipeline_id": opportunity_pipeline_id(opportunity),
        "stage": item_value(opportunity, "pipelineStageName", "stageName", "pipelineStageId"),
        "created_at": item_value(opportunity, "createdAt", "dateAdded"),
        "updated_at": item_value(opportunity, "updatedAt", "lastStatusChangeAt"),
    }


def update_status(client: GHLClient, opportunity_id_value: str, target_status: str) -> dict[str, Any]:
    response = client._request(
        "PUT",
        f"/opportunities/{opportunity_id_value}/status",
        json={"status": target_status},
    )
    payload = response.json()
    return payload.get("opportunity") or payload.get("data") or payload


def run(args: argparse.Namespace) -> dict[str, Any]:
    client = GHLClient(GHLConfig.from_env())
    pipelines = fetch_pipelines(client)
    pipeline = select_pipeline(pipelines, args.pipeline_name)
    pipeline_id = item_value(pipeline, "id", "_id", "pipelineId")
    opportunities = client.fetch_opportunities()
    matches = [
        opportunity
        for opportunity in opportunities
        if opportunity_pipeline_id(opportunity) == pipeline_id
        and opportunity_status(opportunity) == args.from_status.lower()
    ]

    updated: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if not args.dry_run:
        for opportunity in matches:
            oid = opportunity_id(opportunity)
            if not oid:
                errors.append({"opportunity": compact_opportunity(opportunity), "error": "missing opportunity id"})
                continue
            try:
                result = update_status(client, oid, args.to_status.lower())
                updated.append({"before": compact_opportunity(opportunity), "after": compact_opportunity(result)})
            except Exception as exc:  # noqa: BLE001 - command output should include per-record failure.
                errors.append({"opportunity": compact_opportunity(opportunity), "error": str(exc)})

    return {
        "dry_run": args.dry_run,
        "pipeline": {
            "id": pipeline_id,
            "name": item_value(pipeline, "name", "title"),
        },
        "from_status": args.from_status.lower(),
        "to_status": args.to_status.lower(),
        "scanned_opportunities": len(opportunities),
        "matched_count": len(matches),
        "matched": [compact_opportunity(opportunity) for opportunity in matches],
        "updated_count": len(updated),
        "updated": updated,
        "error_count": len(errors),
        "errors": errors,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update opportunity statuses inside one GHL pipeline.")
    parser.add_argument("--pipeline-name", required=True)
    parser.add_argument("--from-status", default="won")
    parser.add_argument("--to-status", default="open")
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> None:
    result = run(parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["dry_run"] and result["error_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
