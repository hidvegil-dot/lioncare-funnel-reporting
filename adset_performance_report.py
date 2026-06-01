from __future__ import annotations

import argparse
import csv
import html
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


TARGET_ADSET_HINT = "LC+ szolgáltatók"
STANDARD_EVENT_HINT = "CompleteRegistration"


@dataclass(frozen=True)
class MetaConfig:
    access_token: str
    ad_account_id: str
    campaign_id: str | None
    api_version: str

    @classmethod
    def from_env(cls) -> "MetaConfig":
        access_token = os.getenv("META_ACCESS_TOKEN", "").strip()
        ad_account_id = os.getenv("META_AD_ACCOUNT_ID", "").strip()
        if not access_token or not ad_account_id:
            raise ValueError("Missing META_ACCESS_TOKEN or META_AD_ACCOUNT_ID")
        return cls(
            access_token=access_token,
            ad_account_id=ad_account_id,
            campaign_id=os.getenv("META_CAMPAIGN_ID", "").strip() or None,
            api_version=os.getenv("META_API_VERSION", "v22.0").strip() or "v22.0",
        )


@dataclass(frozen=True)
class Attribution:
    campaign_id: str | None
    adset_id: str | None
    ad_id: str | None
    source: str | None
    medium: str | None
    url: str | None


class MetaClient:
    def __init__(self, config: MetaConfig) -> None:
        self.config = config
        self.session = requests.Session()

    def fetch_adsets(self) -> list[dict[str, Any]]:
        if self.config.campaign_id:
            url = f"https://graph.facebook.com/{self.config.api_version}/{self.config.campaign_id}/adsets"
        else:
            url = f"https://graph.facebook.com/{self.config.api_version}/{self.config.ad_account_id}/adsets"
        return self._paged_get(
            url,
            {
                "access_token": self.config.access_token,
                "fields": "id,name,status,effective_status,daily_budget,lifetime_budget,campaign_id,campaign{name}",
                "limit": 200,
            },
        )

    def fetch_daily_adset_insights(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        return self._fetch_insights(
            start_date=start_date,
            end_date=end_date,
            level="adset",
            time_increment=1,
        )

    def fetch_platform_insights(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        return self._fetch_insights(
            start_date=start_date,
            end_date=end_date,
            level="adset",
            breakdowns="publisher_platform,platform_position",
        )

    def fetch_demographic_insights(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        return self._fetch_insights(
            start_date=start_date,
            end_date=end_date,
            level="adset",
            breakdowns="age,gender",
        )

    def _fetch_insights(
        self,
        *,
        start_date: date,
        end_date: date,
        level: str,
        time_increment: int | None = None,
        breakdowns: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "access_token": self.config.access_token,
            "level": level,
            "action_report_time": "conversion",
            "fields": ",".join(
                [
                    "date_start",
                    "date_stop",
                    "campaign_id",
                    "campaign_name",
                    "adset_id",
                    "adset_name",
                    "spend",
                    "impressions",
                    "actions",
                ]
            ),
            "time_range": f'{{"since":"{start_date.isoformat()}","until":"{end_date.isoformat()}"}}',
            "limit": 500,
        }
        if time_increment:
            params["time_increment"] = time_increment
        if breakdowns:
            params["breakdowns"] = breakdowns
        if self.config.campaign_id:
            params["filtering"] = f'[{{"field":"campaign.id","operator":"EQUAL","value":"{self.config.campaign_id}"}}]'

        url = f"https://graph.facebook.com/{self.config.api_version}/{self.config.ad_account_id}/insights"
        return self._paged_get(url, params)

    def _paged_get(self, url: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        current_url: str | None = url
        current_params: dict[str, Any] | None = params
        while current_url:
            response = self.session.get(current_url, params=current_params, timeout=45)
            if not response.ok:
                raise RuntimeError(f"Meta API error {response.status_code}: {response.text[:1000]}")
            payload = response.json()
            rows.extend(payload.get("data", []))
            current_url = payload.get("paging", {}).get("next")
            current_params = None
        return rows


def parse_args() -> argparse.Namespace:
    today = date.today()
    yesterday = today - timedelta(days=1)
    parser = argparse.ArgumentParser(description="Compare the two LC+ service provider ad sets.")
    parser.add_argument("--start-date", default="2026-05-19")
    parser.add_argument("--end-date", default=yesterday.isoformat())
    parser.add_argument("--output-dir", default=".")
    return parser.parse_args()


def parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def main() -> None:
    load_dotenv(".env")
    args = parse_args()
    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    meta = MetaClient(MetaConfig.from_env())
    all_adsets = meta.fetch_adsets()
    target_adsets = select_target_adsets(all_adsets)
    if not target_adsets:
        raise RuntimeError(f"No adsets found with hint: {TARGET_ADSET_HINT}")

    target_by_id = {str(adset["id"]): adset for adset in target_adsets}
    target_ids = set(target_by_id)

    daily_meta_rows = [
        normalize_meta_row(row)
        for row in meta.fetch_daily_adset_insights(start_date=start_date, end_date=end_date)
        if str(row.get("adset_id")) in target_ids
    ]
    platform_rows = [
        normalize_meta_row(row)
        for row in meta.fetch_platform_insights(start_date=start_date, end_date=end_date)
        if str(row.get("adset_id")) in target_ids
    ]
    demographic_rows = [
        normalize_meta_row(row)
        for row in meta.fetch_demographic_insights(start_date=start_date, end_date=end_date)
        if str(row.get("adset_id")) in target_ids
    ]

    ghl = GHLClient(GHLConfig.from_env())
    contacts = ghl.fetch_contacts_for_window(start_date=start_date, end_date=end_date)
    custom_field_names = fetch_custom_field_names(ghl)
    lead_rows = build_ghl_lead_rows(contacts, custom_field_names, target_by_id, start_date, end_date)

    daily_rows = build_daily_rows(daily_meta_rows, lead_rows, target_by_id, start_date, end_date)
    totals = build_totals(daily_rows, target_by_id)
    platform_summary = summarize_platforms(platform_rows)
    demographic_summary = summarize_demographics(demographic_rows, lead_rows)

    csv_path = output_dir / "adset_performance_report.csv"
    html_path = output_dir / "adset_performance_report.html"
    write_csv(csv_path, daily_rows)
    write_html(
        html_path,
        start_date=start_date,
        end_date=end_date,
        target_adsets=target_adsets,
        daily_rows=daily_rows,
        totals=totals,
        platform_summary=platform_summary,
        demographic_summary=demographic_summary,
        lead_rows=lead_rows,
    )

    print(f"Target adsets: {', '.join(adset['name'] for adset in target_adsets)}")
    for row in totals:
        print(
            f"{row['adset_name']}: spend {row['spend']:.0f} Ft, "
            f"Meta CR {row['meta_complete_registration']}, Meta CPL {row['meta_cpl']:.0f} Ft, "
            f"GHL lead {row['ghl_leads']}, GHL CPL {row['ghl_cpl']:.0f} Ft"
        )
    print(f"CSV: {csv_path}")
    print(f"HTML: {html_path}")


def select_target_adsets(adsets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [
        adset
        for adset in adsets
        if TARGET_ADSET_HINT.lower() in str(adset.get("name") or "").lower()
    ]
    active = [
        adset
        for adset in candidates
        if str(adset.get("effective_status") or adset.get("status") or "").upper() == "ACTIVE"
    ]
    selected = active or candidates

    def sort_key(adset: dict[str, Any]) -> tuple[int, str]:
        name = str(adset.get("name") or "")
        return (0 if STANDARD_EVENT_HINT.lower() in name.lower() else 1, name)

    return sorted(selected, key=sort_key)[:2]


def normalize_meta_row(row: dict[str, Any]) -> dict[str, Any]:
    actions = action_lookup(row.get("actions"))
    complete_registration = max(
        actions.get("complete_registration", 0),
        actions.get("omni_complete_registration", 0),
        actions.get("offsite_conversion.fb_pixel_complete_registration", 0),
        actions.get("offsite_complete_registration_add_meta_leads", 0),
    )
    return {
        **row,
        "spend": to_float(row.get("spend")),
        "impressions": to_int(row.get("impressions")),
        "link_click": actions.get("link_click", 0),
        "landing_page_view": actions.get("landing_page_view", 0),
        "meta_complete_registration": complete_registration,
    }


def action_lookup(actions: Any) -> dict[str, int]:
    lookup: dict[str, int] = {}
    if not isinstance(actions, list):
        return lookup
    for item in actions:
        if not isinstance(item, dict):
            continue
        action_type = str(item.get("action_type") or "").strip()
        if action_type:
            lookup[action_type] = to_int(item.get("value"))
    return lookup


def build_ghl_lead_rows(
    contacts: list[dict[str, Any]],
    custom_field_names: dict[str, str],
    target_by_id: dict[str, dict[str, Any]],
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for contact in contacts:
        lead_date = contact.get("lead_date")
        if not isinstance(lead_date, date):
            continue
        if not (start_date <= lead_date <= end_date):
            continue
        raw = contact.get("raw") or {}
        attribution = extract_attribution(raw)
        adset_id = attribution.adset_id or "ismeretlen"
        adset = target_by_id.get(adset_id)
        if not adset:
            continue
        age, gender = extract_demographics(raw, custom_field_names)
        rows.append(
            {
                "date": lead_date.isoformat(),
                "adset_id": adset_id,
                "adset_name": adset.get("name") or adset_id,
                "source": normalize_source(attribution.source, attribution.medium),
                "age": age or "ismeretlen",
                "gender": gender or "ismeretlen",
                "booked": 1 if contact.get("first_booking_date") else 0,
                "showed": 1 if contact.get("show_date") else 0,
                "closed": 1 if contact.get("close_date") else 0,
            }
        )
    return rows


def extract_attribution(raw: dict[str, Any]) -> Attribution:
    urls: list[str] = []
    source: str | None = None
    medium: str | None = None
    for key in ("attributionSource", "lastAttributionSource", "attributions"):
        value = raw.get(key)
        if isinstance(value, dict):
            source = source or value.get("utmSource") or value.get("source") or value.get("sessionSource")
            medium = medium or value.get("medium")
            url = value.get("url") or value.get("utmUrl")
            if url:
                urls.append(str(url))
        elif isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                source = source or item.get("utmSource") or item.get("source") or item.get("sessionSource")
                medium = medium or item.get("medium")
                url = item.get("url") or item.get("utmUrl")
                if url:
                    urls.append(str(url))

    selected_url = urls[0] if urls else None
    query = parse_qs(urlparse(selected_url).query) if selected_url else {}
    return Attribution(
        campaign_id=first(query, "utm_campaign") or first(query, "utm_id"),
        adset_id=first(query, "utm_term"),
        ad_id=first(query, "utm_content"),
        source=first(query, "utm_source") or source or raw.get("source"),
        medium=medium,
        url=selected_url,
    )


def first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key) or []
    return values[0] if values else None


def fetch_custom_field_names(client: GHLClient) -> dict[str, str]:
    response = client._request("GET", f"/locations/{client.config.location_id}/customFields")
    payload = response.json()
    fields = payload.get("customFields") or payload.get("fields") or payload.get("data") or []
    names: dict[str, str] = {}
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or field.get("fieldKey") or field.get("key") or "").strip()
        for key in (field.get("id"), field.get("fieldKey"), field.get("key")):
            if key and name:
                names[str(key)] = name
    return names


def extract_demographics(raw: dict[str, Any], custom_field_names: dict[str, str]) -> tuple[str | None, str | None]:
    age: str | None = None
    gender: str | None = None
    custom_fields = raw.get("customFields") or raw.get("custom_fields") or []
    if not isinstance(custom_fields, list):
        return age, gender
    for field in custom_fields:
        if not isinstance(field, dict):
            continue
        field_id = str(field.get("id") or field.get("fieldId") or field.get("customFieldId") or "")
        name = custom_field_names.get(field_id, field_id).lower()
        value = field.get("value")
        if value in (None, ""):
            continue
        text = str(value).strip()
        if not age and any(token in name for token in ("kor", "age")):
            age = text
        if not gender and any(token in name for token in ("nem", "gender", "neme")):
            gender = text
    return age, gender


def normalize_source(source: str | None, medium: str | None) -> str:
    tokens = {part.lower() for part in (source, medium) if part}
    text = " ".join(sorted(tokens))
    if "instagram" in text or "ig" in tokens:
        return "Instagram"
    if "facebook" in text or "fb" in tokens:
        return "Facebook"
    return source or medium or "ismeretlen"


def build_daily_rows(
    meta_rows: list[dict[str, Any]],
    lead_rows: list[dict[str, Any]],
    target_by_id: dict[str, dict[str, Any]],
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    meta_by_day_adset = {(row.get("date_start"), str(row.get("adset_id"))): row for row in meta_rows}
    ghl_counts: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for lead in lead_rows:
        counter = ghl_counts[(lead["date"], lead["adset_id"])]
        counter["ghl_leads"] += 1
        counter["booked"] += lead["booked"]
        counter["showed"] += lead["showed"]
        counter["closed"] += lead["closed"]

    rows: list[dict[str, Any]] = []
    current = start_date
    while current <= end_date:
        day = current.isoformat()
        for adset_id, adset in target_by_id.items():
            meta = meta_by_day_adset.get((day, adset_id), {})
            ghl = ghl_counts.get((day, adset_id), Counter())
            spend = to_float(meta.get("spend"))
            meta_cr = to_int(meta.get("meta_complete_registration"))
            ghl_leads = int(ghl.get("ghl_leads", 0))
            rows.append(
                {
                    "date": day,
                    "adset_id": adset_id,
                    "adset_name": adset.get("name") or adset_id,
                    "spend": spend,
                    "impressions": to_int(meta.get("impressions")),
                    "link_click": to_int(meta.get("link_click")),
                    "landing_page_view": to_int(meta.get("landing_page_view")),
                    "meta_complete_registration": meta_cr,
                    "meta_cpl": round(spend / meta_cr, 2) if meta_cr else 0.0,
                    "ghl_leads": ghl_leads,
                    "ghl_cpl": round(spend / ghl_leads, 2) if ghl_leads else 0.0,
                    "booked": int(ghl.get("booked", 0)),
                    "showed": int(ghl.get("showed", 0)),
                    "closed": int(ghl.get("closed", 0)),
                }
            )
        current += timedelta(days=1)
    return rows


def build_totals(daily_rows: list[dict[str, Any]], target_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    counters: dict[str, Counter] = defaultdict(Counter)
    for row in daily_rows:
        counter = counters[row["adset_id"]]
        for key in ("impressions", "link_click", "landing_page_view", "meta_complete_registration", "ghl_leads", "booked", "showed", "closed"):
            counter[key] += int(row[key])
        counter["spend"] += float(row["spend"])

    rows: list[dict[str, Any]] = []
    for adset_id, adset in target_by_id.items():
        counter = counters[adset_id]
        spend = float(counter["spend"])
        meta_cr = int(counter["meta_complete_registration"])
        ghl_leads = int(counter["ghl_leads"])
        rows.append(
            {
                "adset_id": adset_id,
                "adset_name": adset.get("name") or adset_id,
                "budget": format_budget(adset),
                "spend": spend,
                "impressions": int(counter["impressions"]),
                "link_click": int(counter["link_click"]),
                "landing_page_view": int(counter["landing_page_view"]),
                "meta_complete_registration": meta_cr,
                "meta_cpl": round(spend / meta_cr, 2) if meta_cr else 0.0,
                "ghl_leads": ghl_leads,
                "ghl_cpl": round(spend / ghl_leads, 2) if ghl_leads else 0.0,
                "booked": int(counter["booked"]),
                "showed": int(counter["showed"]),
                "closed": int(counter["closed"]),
            }
        )
    return rows


def summarize_platforms(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counters: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for row in rows:
        key = (str(row.get("adset_name") or row.get("adset_id")), normalize_source(row.get("publisher_platform"), row.get("platform_position")))
        counter = counters[key]
        counter["spend"] += float(row["spend"])
        counter["meta_complete_registration"] += int(row["meta_complete_registration"])
        counter["link_click"] += int(row["link_click"])
    return [
        {
            "adset_name": adset_name,
            "platform": platform,
            "spend": float(counter["spend"]),
            "meta_complete_registration": int(counter["meta_complete_registration"]),
            "link_click": int(counter["link_click"]),
        }
        for (adset_name, platform), counter in sorted(counters.items())
    ]


def summarize_demographics(meta_rows: list[dict[str, Any]], lead_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    meta_counter: dict[tuple[str, str, str], Counter] = defaultdict(Counter)
    for row in meta_rows:
        key = (
            str(row.get("adset_name") or row.get("adset_id")),
            str(row.get("age") or "ismeretlen"),
            str(row.get("gender") or "ismeretlen"),
        )
        meta_counter[key]["spend"] += float(row["spend"])
        meta_counter[key]["meta_complete_registration"] += int(row["meta_complete_registration"])

    ghl_counter: dict[tuple[str, str, str], Counter] = defaultdict(Counter)
    for lead in lead_rows:
        key = (lead["adset_name"], lead["age"], lead["gender"])
        ghl_counter[key]["ghl_leads"] += 1
        ghl_counter[key]["booked"] += lead["booked"]

    return {
        "meta": [
            {
                "adset_name": adset_name,
                "age": age,
                "gender": gender,
                "spend": float(counter["spend"]),
                "meta_complete_registration": int(counter["meta_complete_registration"]),
            }
            for (adset_name, age, gender), counter in sorted(
                meta_counter.items(), key=lambda item: (-item[1]["meta_complete_registration"], item[0])
            )
        ],
        "ghl": [
            {
                "adset_name": adset_name,
                "age": age,
                "gender": gender,
                "ghl_leads": int(counter["ghl_leads"]),
                "booked": int(counter["booked"]),
            }
            for (adset_name, age, gender), counter in sorted(
                ghl_counter.items(), key=lambda item: (-item[1]["ghl_leads"], item[0])
            )
        ],
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "date",
                "adset_name",
                "spend",
                "impressions",
                "link_click",
                "landing_page_view",
                "meta_complete_registration",
                "meta_cpl",
                "ghl_leads",
                "ghl_cpl",
                "booked",
                "showed",
                "closed",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in writer.fieldnames})


def write_html(
    path: Path,
    *,
    start_date: date,
    end_date: date,
    target_adsets: list[dict[str, Any]],
    daily_rows: list[dict[str, Any]],
    totals: list[dict[str, Any]],
    platform_summary: list[dict[str, Any]],
    demographic_summary: dict[str, list[dict[str, Any]]],
    lead_rows: list[dict[str, Any]],
) -> None:
    css = """
    body{font-family:Inter,Arial,sans-serif;margin:32px;color:#13233a;background:#fbfaf7}
    h1,h2{font-family:Georgia,serif;color:#10294a} table{border-collapse:collapse;width:100%;margin:14px 0 28px;background:white}
    th,td{border:1px solid #e4dac8;padding:10px;text-align:left;font-size:14px} th{background:#f1eadf}
    .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;margin:18px 0}
    .card{background:white;border:1px solid #d8c8ad;border-radius:16px;padding:18px;box-shadow:0 10px 30px rgba(16,41,74,.06)}
    .num{font-size:30px;font-weight:800;color:#10294a}.muted{color:#657083}.warn{color:#9a5b00}
    """
    total_cards = "".join(
        f"""
        <div class="card">
          <h3>{esc(row['adset_name'])}</h3>
          <div class="muted">Budget: {esc(row['budget'])}</div>
          <p>Költés: <span class="num">{fmt(row['spend'])} Ft</span></p>
          <p>Meta CompleteRegistration: <b>{row['meta_complete_registration']}</b> | Meta CPL: <b>{fmt(row['meta_cpl'])} Ft</b></p>
          <p>GHL lead: <b>{row['ghl_leads']}</b> | GHL CPL: <b>{fmt(row['ghl_cpl'])} Ft</b></p>
          <p>Booking/show/closed: {row['booked']} / {row['showed']} / {row['closed']}</p>
        </div>
        """
        for row in totals
    )
    body = f"""
    <!doctype html><html><head><meta charset="utf-8"><title>Adset teljesítmény riport</title><style>{css}</style></head>
    <body>
    <h1>LC+ hirdetéssorozat összehasonlítás</h1>
    <p class="muted">Időszak: {start_date.isoformat()} - {end_date.isoformat()}. A kezdő dátum feltételezés: ez az a szakasz, amióta a két sorozat párhuzamos, napi 4000 Ft körüli budgettel fut.</p>
    <div class="cards">{total_cards}</div>
    <h2>Napi bontás</h2>
    {table(daily_rows, ['date','adset_name','spend','meta_complete_registration','meta_cpl','ghl_leads','ghl_cpl','booked','showed','closed'])}
    <h2>Facebook / Instagram bontás Meta CompleteRegistration alapján</h2>
    {table(platform_summary, ['adset_name','platform','spend','link_click','meta_complete_registration'])}
    <h2>Meta demográfia CompleteRegistration alapján</h2>
    {table(demographic_summary['meta'][:30], ['adset_name','age','gender','spend','meta_complete_registration'])}
    <h2>GHL lead demográfia</h2>
    {table(demographic_summary['ghl'][:30], ['adset_name','age','gender','ghl_leads','booked'])}
    <h2>GHL leadek platform/source szerint</h2>
    {table(summarize_ghl_sources(lead_rows), ['adset_name','source','ghl_leads','booked'])}
    <p class="muted">Megjegyzés: a GHL lead hirdetéssorozathoz kötése az URL UTM-ek alapján történik: utm_term = adset ID. Ha egy leadnél nincs UTM, az nem kerül ebbe az adset-összehasonlításba.</p>
    </body></html>
    """
    path.write_text(body, encoding="utf-8")


def summarize_ghl_sources(lead_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counters: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for lead in lead_rows:
        counter = counters[(lead["adset_name"], lead["source"])]
        counter["ghl_leads"] += 1
        counter["booked"] += lead["booked"]
    return [
        {
            "adset_name": adset_name,
            "source": source,
            "ghl_leads": int(counter["ghl_leads"]),
            "booked": int(counter["booked"]),
        }
        for (adset_name, source), counter in sorted(counters.items())
    ]


def table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return '<p class="warn">Nincs adat ebben a bontásban.</p>'
    header = "".join(f"<th>{esc(col)}</th>" for col in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{esc(format_cell(row.get(col)))}</td>" for col in columns) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"


def format_budget(adset: dict[str, Any]) -> str:
    raw = adset.get("daily_budget") or adset.get("lifetime_budget")
    if raw in (None, ""):
        return "nincs API adat"
    return f"{to_float(raw):.0f} Ft"


def format_cell(value: Any) -> str:
    if isinstance(value, float):
        return fmt(value)
    return str(value)


def fmt(value: float | int) -> str:
    return f"{float(value):,.0f}".replace(",", " ")


def esc(value: Any) -> str:
    return html.escape(str(value))


def to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def to_float(value: Any) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    main()
