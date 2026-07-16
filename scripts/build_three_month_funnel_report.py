from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghl_client import GHLClient, GHLConfig
from meta_ads_client import MetaAdsClient, MetaAdsConfig
from report_builder import (
    build_report_rows,
    overlay_funnel_counts_from_appointments,
    summarize_period,
)


def parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def month_key(day: date) -> str:
    return day.strftime("%Y-%m")


def month_bounds(start_date: date, end_date: date) -> list[tuple[date, date]]:
    bounds: list[tuple[date, date]] = []
    cursor = date(start_date.year, start_date.month, 1)
    while cursor <= end_date:
        next_month = date(cursor.year + (cursor.month // 12), (cursor.month % 12) + 1, 1)
        bounds.append((max(start_date, cursor), min(end_date, next_month.fromordinal(next_month.toordinal() - 1))))
        cursor = next_month
    return bounds


def safe_pct(numerator: float, denominator: float) -> float:
    return round((numerator / denominator) * 100, 2) if denominator else 0.0


def safe_div(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 2) if denominator else 0.0


def aggregate_rows_by_month(rows: list[dict[str, Any]]) -> dict[str, Counter]:
    monthly: dict[str, Counter] = {}
    for row in rows:
        day = parse_date(str(row["date"]))
        key = month_key(day)
        monthly.setdefault(key, Counter())
        for field in ("new_leads", "booked_leads", "showed_leads", "closed_leads"):
            monthly[key][field] += int(row.get(field, 0))
    return monthly


def fetch_meta_by_month(meta_client: MetaAdsClient, start_date: date, end_date: date) -> dict[str, dict[str, Any]]:
    monthly: dict[str, dict[str, Any]] = {}
    for month_start, month_end in month_bounds(start_date, end_date):
        meta = meta_client.fetch_summary_and_breakdowns(start_date=month_start, end_date=month_end)
        monthly[month_key(month_start)] = meta.get("summary") or {}
    return monthly


def build_monthly_rows(
    *,
    ghl_monthly: dict[str, Counter],
    meta_monthly: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in sorted(set(ghl_monthly) | set(meta_monthly)):
        ghl = ghl_monthly.get(key, Counter())
        meta = meta_monthly.get(key, {})
        spend = round(float(meta.get("spend", 0.0)), 2)
        leads = int(ghl.get("new_leads", 0))
        booked = int(ghl.get("booked_leads", 0))
        showed = int(ghl.get("showed_leads", 0))
        rows.append(
            {
                "month": key,
                "spend_huf": spend,
                "ghl_leads": leads,
                "booked_meets": booked,
                "showed_meets": showed,
                "ghl_cpl_huf": safe_div(spend, leads),
                "lead_to_booked_pct": safe_pct(booked, leads),
                "booked_to_showed_pct": safe_pct(showed, booked),
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "month",
        "spend_huf",
        "ghl_leads",
        "booked_meets",
        "showed_meets",
        "ghl_cpl_huf",
        "lead_to_booked_pct",
        "booked_to_showed_pct",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def fmt_huf(value: float) -> str:
    return f"{value:,.0f} Ft".replace(",", " ")


def fmt_num(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def write_html(path: Path, *, start_date: date, end_date: date, totals: dict[str, Any], monthly_rows: list[dict[str, Any]]) -> None:
    table_rows = "\n".join(
        "<tr>"
        f"<td>{row['month']}</td>"
        f"<td>{fmt_huf(row['spend_huf'])}</td>"
        f"<td>{row['ghl_leads']}</td>"
        f"<td>{row['booked_meets']}</td>"
        f"<td>{row['showed_meets']}</td>"
        f"<td>{fmt_huf(row['ghl_cpl_huf'])}</td>"
        f"<td>{row['lead_to_booked_pct']}%</td>"
        f"<td>{row['booked_to_showed_pct']}%</td>"
        "</tr>"
        for row in monthly_rows
    )
    html = f"""<!doctype html>
<html lang="hu">
<head>
  <meta charset="utf-8">
  <title>LionCare 3 havi funnel riport</title>
  <style>
    body {{ font-family: Arial, sans-serif; color: #172033; margin: 32px; line-height: 1.45; }}
    h1 {{ margin-bottom: 4px; }}
    .muted {{ color: #667085; }}
    .cards {{ display: grid; grid-template-columns: repeat(4, minmax(160px, 1fr)); gap: 12px; margin: 24px 0; }}
    .card {{ border: 1px solid #d0d5dd; border-radius: 8px; padding: 14px; }}
    .label {{ color: #667085; font-size: 13px; }}
    .value {{ font-size: 24px; font-weight: 700; margin-top: 6px; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 18px; }}
    th, td {{ border-bottom: 1px solid #eaecf0; padding: 10px; text-align: right; }}
    th:first-child, td:first-child {{ text-align: left; }}
    th {{ background: #f8fafc; }}
  </style>
</head>
<body>
  <h1>LionCare 3 havi funnel riport</h1>
  <div class="muted">Időszak: {start_date.isoformat()} - {end_date.isoformat()} | Forrás: Meta Ads + GHL</div>
  <h2>Executive Summary</h2>
  <p><strong>Az időszakban {fmt_huf(totals['spend_huf'])} költésből {totals['ghl_leads']} GHL lead érkezett.</strong> Ez {fmt_huf(totals['ghl_cpl_huf'])} átlagos GHL lead költséget jelent.</p>
  <p><strong>A leadekből {totals['booked_meets']} meet lett leszervezve, ebből {totals['showed_meets']} showed.</strong> Lead -> booked arány: {totals['lead_to_booked_pct']}%, booked -> showed arány: {totals['booked_to_showed_pct']}%.</p>

  <div class="cards">
    <div class="card"><div class="label">Költés</div><div class="value">{fmt_huf(totals['spend_huf'])}</div></div>
    <div class="card"><div class="label">GHL lead</div><div class="value">{fmt_num(totals['ghl_leads'])}</div></div>
    <div class="card"><div class="label">Leszervezett meet</div><div class="value">{fmt_num(totals['booked_meets'])}</div></div>
    <div class="card"><div class="label">Showed meet</div><div class="value">{fmt_num(totals['showed_meets'])}</div></div>
  </div>

  <h2>Havi bontás</h2>
  <table>
    <thead>
      <tr>
        <th>Hónap</th>
        <th>Költés</th>
        <th>GHL lead</th>
        <th>Meet booked</th>
        <th>Meet showed</th>
        <th>GHL CPL</th>
        <th>Lead -> booked</th>
        <th>Booked -> showed</th>
      </tr>
    </thead>
    <tbody>{table_rows}</tbody>
  </table>

  <h2>Caveat</h2>
  <p class="muted">A lead szám GHL `lead_date` alapján, a booked/showed szám GHL appointment események alapján készült. A költés Meta Ads API-ból jön a beállított kampányszűréssel.</p>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a 3-month LionCare funnel report from GHL and Meta Ads.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output-dir", default=".")
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    ghl = GHLClient(GHLConfig.from_env())
    contacts = ghl.fetch_contacts_for_window(start_date=start_date, end_date=end_date)
    closed_meeting_counts = ghl.fetch_closed_contact_meeting_counts(
        contacts=contacts,
        start_date=start_date,
        end_date=end_date,
    )
    rows = build_report_rows(
        contacts=contacts,
        closed_meeting_counts=closed_meeting_counts,
        start_date=start_date,
        end_date=end_date,
    )
    all_contacts = ghl.fetch_all_contacts()
    appointments = ghl.fetch_appointments_for_contacts(
        contacts=all_contacts,
        start_date=start_date,
        end_date=end_date,
    )
    rows = overlay_funnel_counts_from_appointments(rows=rows, appointments=appointments)
    summary = summarize_period(rows=rows, closed_meeting_counts=closed_meeting_counts)

    meta_config = MetaAdsConfig.from_env_optional()
    if meta_config is None:
        raise RuntimeError("Missing Meta Ads environment variables")
    meta = MetaAdsClient(meta_config)
    meta_summary = meta.fetch_summary_and_breakdowns(start_date=start_date, end_date=end_date)["summary"]
    meta_monthly = fetch_meta_by_month(meta, start_date, end_date)
    ghl_monthly = aggregate_rows_by_month(rows)
    monthly_rows = build_monthly_rows(ghl_monthly=ghl_monthly, meta_monthly=meta_monthly)

    spend = round(float(meta_summary.get("spend", 0.0)), 2)
    leads = int(summary.get("new_leads", 0))
    booked = int(summary.get("booked_leads", 0))
    showed = int(summary.get("showed_leads", 0))
    totals = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "spend_huf": spend,
        "ghl_leads": leads,
        "booked_meets": booked,
        "showed_meets": showed,
        "ghl_cpl_huf": safe_div(spend, leads),
        "lead_to_booked_pct": safe_pct(booked, leads),
        "booked_to_showed_pct": safe_pct(showed, booked),
    }

    write_csv(output_dir / "three_month_funnel_report.csv", monthly_rows)
    write_html(
        output_dir / "three_month_funnel_report.html",
        start_date=start_date,
        end_date=end_date,
        totals=totals,
        monthly_rows=monthly_rows,
    )
    (output_dir / "three_month_funnel_report.json").write_text(
        json.dumps({"totals": totals, "monthly": monthly_rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"totals": totals, "monthly": monthly_rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
