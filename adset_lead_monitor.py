from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import load_dotenv

from ghl_client import GHLClient, GHLConfig


META_API_VERSION = os.getenv("META_API_VERSION", "v22.0")
CUSTOM_CONVERSION_ADSET_HINT = "LC+ szolgáltatók"
STANDARD_EVENT_ADSET_HINT = "CompleteRegistration"


@dataclass(frozen=True)
class Attribution:
    campaign_id: str | None
    adset_id: str | None
    ad_id: str | None
    source: str | None
    url: str | None


def parse_args() -> argparse.Namespace:
    today = date.today()
    yesterday = today - timedelta(days=1)
    default_start = yesterday - timedelta(days=6)
    parser = argparse.ArgumentParser(description="Monitor GHL leads by Meta ad set attribution.")
    parser.add_argument("--start-date", default=default_start.isoformat())
    parser.add_argument("--end-date", default=yesterday.isoformat())
    parser.add_argument("--output-dir", default=".")
    return parser.parse_args()


def parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def extract_attribution(raw: dict[str, Any]) -> Attribution:
    urls: list[str] = []
    for key in ("attributionSource", "lastAttributionSource", "attributions"):
        value = raw.get(key)
        if isinstance(value, dict):
            url = value.get("url") or value.get("utmUrl")
            if url:
                urls.append(str(url))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    url = item.get("url") or item.get("utmUrl")
                    if url:
                        urls.append(str(url))

    selected_url = urls[0] if urls else None
    query = parse_qs(urlparse(selected_url).query) if selected_url else {}
    return Attribution(
        campaign_id=first(query, "utm_campaign") or first(query, "utm_id"),
        adset_id=first(query, "utm_term"),
        ad_id=first(query, "utm_content"),
        source=first(query, "utm_source") or raw.get("source"),
        url=selected_url,
    )


def first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key) or []
    return values[0] if values else None


def fetch_meta_names(ids: set[str], access_token: str) -> dict[str, str]:
    names: dict[str, str] = {}
    session = requests.Session()
    for object_id in sorted(item for item in ids if item):
        try:
            response = session.get(
                f"https://graph.facebook.com/{META_API_VERSION}/{object_id}",
                params={"access_token": access_token, "fields": "name"},
                timeout=30,
            )
            if response.ok:
                payload = response.json()
                if payload.get("name"):
                    names[object_id] = payload["name"]
        except requests.RequestException:
            continue
    return names


def classify_adset(name: str, adset_id: str | None) -> str:
    lower = name.lower()
    if STANDARD_EVENT_ADSET_HINT.lower() in lower:
        return "mostani CompleteRegistration"
    if CUSTOM_CONVERSION_ADSET_HINT.lower() in lower:
        return "régi egyéni konverziós"
    if adset_id:
        return f"egyéb / {adset_id}"
    return "ismeretlen"


def main() -> None:
    load_dotenv()
    args = parse_args()
    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    client = GHLClient(GHLConfig.from_env())
    contacts = client.fetch_contacts_for_window(start_date=start_date, end_date=end_date)

    rows: list[dict[str, Any]] = []
    adset_ids: set[str] = set()
    ad_ids: set[str] = set()
    campaign_ids: set[str] = set()
    for contact in contacts:
        lead_date = contact.get("lead_date")
        if not isinstance(lead_date, date) or not (start_date <= lead_date <= end_date):
            continue
        raw = contact.get("raw") or {}
        attribution = extract_attribution(raw)
        if attribution.adset_id:
            adset_ids.add(attribution.adset_id)
        if attribution.ad_id:
            ad_ids.add(attribution.ad_id)
        if attribution.campaign_id:
            campaign_ids.add(attribution.campaign_id)

        rows.append(
            {
                "lead_date": lead_date,
                "booking_date": contact.get("first_booking_date"),
                "show_date": contact.get("show_date"),
                "close_date": contact.get("close_date"),
                "adset_id": attribution.adset_id or "ismeretlen",
                "ad_id": attribution.ad_id or "ismeretlen",
                "campaign_id": attribution.campaign_id or "ismeretlen",
                "source": attribution.source or "ismeretlen",
                "landing_page_url": contact.get("landing_page_url") or "ismeretlen",
            }
        )

    access_token = os.getenv("META_ACCESS_TOKEN", "").strip()
    adset_names = fetch_meta_names(adset_ids, access_token) if access_token else {}
    ad_names = fetch_meta_names(ad_ids, access_token) if access_token else {}
    campaign_names = fetch_meta_names(campaign_ids, access_token) if access_token else {}

    for row in rows:
        row["adset_name"] = adset_names.get(row["adset_id"], row["adset_id"])
        row["ad_name"] = ad_names.get(row["ad_id"], row["ad_id"])
        row["campaign_name"] = campaign_names.get(row["campaign_id"], row["campaign_id"])
        row["adset_group"] = classify_adset(row["adset_name"], row["adset_id"])

    daily_adset: dict[tuple[str, str], Counter] = defaultdict(Counter)
    adset_totals: dict[str, Counter] = defaultdict(Counter)
    ad_totals: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        adset_key = row["adset_name"]
        ad_key = row["ad_name"]
        day_key = row["lead_date"].isoformat()
        counters = (
            daily_adset[(day_key, adset_key)],
            adset_totals[adset_key],
            ad_totals[ad_key],
        )
        for counter in counters:
            counter["leads"] += 1
            if row["booking_date"]:
                counter["booked"] += 1
            if row["show_date"]:
                counter["showed"] += 1
            if row["close_date"]:
                counter["closed"] += 1

    csv_path = output_dir / "adset_lead_monitor.csv"
    md_path = output_dir / "adset_lead_monitor.md"

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "lead_date",
                "adset_group",
                "adset_name",
                "ad_name",
                "source",
                "landing_page_url",
                "booked",
                "showed",
                "closed",
            ],
        )
        writer.writeheader()
        for row in sorted(rows, key=lambda item: (item["lead_date"], item["adset_name"], item["ad_name"])):
            writer.writerow(
                {
                    "lead_date": row["lead_date"].isoformat(),
                    "adset_group": row["adset_group"],
                    "adset_name": row["adset_name"],
                    "ad_name": row["ad_name"],
                    "source": row["source"],
                    "landing_page_url": row["landing_page_url"],
                    "booked": 1 if row["booking_date"] else 0,
                    "showed": 1 if row["show_date"] else 0,
                    "closed": 1 if row["close_date"] else 0,
                }
            )

    lines = [
        f"# Adset lead monitor ({start_date.isoformat()} - {end_date.isoformat()})",
        "",
        "## Hirdetéssorozat összesítő",
    ]
    for adset_name, counter in sorted(adset_totals.items(), key=lambda item: (-item[1]["leads"], item[0])):
        lines.append(
            f"- {adset_name}: lead {counter['leads']}, booking {counter['booked']}, "
            f"showed {counter['showed']}, closed {counter['closed']}, "
            f"booking arány {pct(counter['booked'], counter['leads'])}%"
        )

    lines.extend(["", "## Napi bontás hirdetéssorozat szerint"])
    for (day, adset_name), counter in sorted(daily_adset.items()):
        lines.append(
            f"- {day} | {adset_name}: lead {counter['leads']}, booking {counter['booked']}, "
            f"showed {counter['showed']}, closed {counter['closed']}"
        )

    lines.extend(["", "## Hirdetés szintű bontás"])
    for ad_name, counter in sorted(ad_totals.items(), key=lambda item: (-item[1]["leads"], item[0]))[:20]:
        lines.append(
            f"- {ad_name}: lead {counter['leads']}, booking {counter['booked']}, showed {counter['showed']}, closed {counter['closed']}"
        )

    lines.extend(
        [
            "",
            "## Megjegyzés",
            "- A riport GHL attribution URL alapján köt leadet Meta adsethez: utm_term = adset ID, utm_content = ad ID.",
            "- A booking/show/closed itt a lead cohort minősége: az adott adsetből jött leadnél van-e ilyen GHL mező.",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines))
    print(f"\nCSV: {csv_path}")
    print(f"MD: {md_path}")


def pct(numerator: int, denominator: int) -> float:
    return round((numerator / denominator) * 100, 2) if denominator else 0.0


if __name__ == "__main__":
    main()
