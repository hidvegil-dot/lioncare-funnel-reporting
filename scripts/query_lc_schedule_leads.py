from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adset_performance_report import MetaClient, MetaConfig, extract_attribution
from ghl_client import GHLClient, GHLConfig


def parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def find_schedule_adsets(meta: MetaClient, name_contains: str) -> list[dict[str, Any]]:
    adsets = meta.fetch_adsets()
    name_tokens = [token for token in normalize(name_contains).replace("–", "-").split() if token]

    matches = []
    for adset in adsets:
        name = normalize(adset.get("name")).replace("–", "-")
        if all(token in name for token in name_tokens):
            matches.append(adset)

    if matches:
        return matches

    fallback = []
    for adset in adsets:
        name = normalize(adset.get("name")).replace("–", "-")
        if "lc+" in name and "szolg" in name and "schedule" in name:
            fallback.append(adset)
    return fallback


def count_ghl_leads_for_adsets(
    *,
    contacts: list[dict[str, Any]],
    adset_ids: set[str],
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    by_adset: Counter[str] = Counter()
    total = 0
    unattributed_in_window = 0

    for contact in contacts:
        lead_date = contact.get("lead_date")
        if not isinstance(lead_date, date):
            continue
        if not (start_date <= lead_date <= end_date):
            continue
        attribution = extract_attribution(contact.get("raw") or {})
        if not attribution.adset_id:
            unattributed_in_window += 1
            continue
        if attribution.adset_id in adset_ids:
            by_adset[attribution.adset_id] += 1
            total += 1

    return {
        "ghl_leads": total,
        "by_adset_id": dict(by_adset),
        "unattributed_leads_in_window": unattributed_in_window,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Count GHL leads for LC+ Schedule adset series.")
    parser.add_argument("--start-date", default="2026-01-01")
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--name-contains", default="LC+ szolgáltatók Schedule")
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)

    meta = MetaClient(MetaConfig.from_env())
    adsets = find_schedule_adsets(meta, args.name_contains)
    adset_ids = {str(adset.get("id")) for adset in adsets if adset.get("id")}
    if not adset_ids:
        raise RuntimeError(f"No Meta adsets found for name filter: {args.name_contains}")

    ghl = GHLClient(GHLConfig.from_env())
    contacts = ghl.fetch_contacts_for_window(start_date=start_date, end_date=end_date)
    counts = count_ghl_leads_for_adsets(
        contacts=contacts,
        adset_ids=adset_ids,
        start_date=start_date,
        end_date=end_date,
    )
    result = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "name_filter": args.name_contains,
        "matched_adsets": [
            {
                "id": str(adset.get("id") or ""),
                "name": str(adset.get("name") or ""),
                "status": str(adset.get("status") or ""),
                "effective_status": str(adset.get("effective_status") or ""),
            }
            for adset in adsets
        ],
        **counts,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
