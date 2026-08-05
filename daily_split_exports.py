from __future__ import annotations

import csv
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


BUDAPEST_TZ = ZoneInfo("Europe/Budapest")

DAILY_AD_PERFORMANCE_COLUMNS = [
    "date",
    "platform",
    "campaign_id",
    "campaign_name",
    "adset_id",
    "adset_name",
    "ad_id",
    "ad_name",
    "spend_huf",
    "impressions",
    "reach",
    "clicks",
    "link_clicks",
    "landing_page_views",
    "registration_leads",
    "exported_at",
]

LEAD_COHORT_COLUMNS = [
    "lead_id",
    "lead_created_at",
    "source",
    "medium",
    "attribution_status",
    "campaign_id",
    "campaign_name",
    "adset_id",
    "adset_name",
    "ad_id",
    "ad_name",
    "booking_created_at",
    "appointment_at",
    "showed_at",
    "contract_created_at",
    "current_status",
    "exported_at",
]

BACKFILL_QA_COLUMNS = [
    "date",
    "source_file",
    "ad_performance_file",
    "lead_cohort_file",
    "source_rows",
    "unique_leads",
    "unique_date_ad_id",
    "deduped_spend_huf",
    "landing_page_views",
    "meta_registration_leads",
    "ghl_leads",
    "bookings",
    "leads_without_source",
    "duplicate_lead_ids",
    "ad_data_conflicts",
    "missing_required_fields",
]

AD_REQUIRED_FIELDS = [
    "date",
    "platform",
    "ad_id",
]

LEAD_REQUIRED_FIELDS = [
    "lead_id",
    "lead_created_at",
    "attribution_status",
    "current_status",
]


def current_budapest_timestamp() -> str:
    return datetime.now(tz=BUDAPEST_TZ).replace(microsecond=0).isoformat()


def build_daily_ad_performance_rows(
    *,
    report_date: date,
    meta_data: dict[str, Any] | None,
    exported_at: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    for meta_row in (meta_data or {}).get("ads") or []:
        ad_id = _clean(meta_row.get("ad_id"))
        if not ad_id:
            continue
        rows.append(
            {
                "date": report_date.isoformat(),
                "platform": "Meta",
                "campaign_id": _clean(meta_row.get("campaign_id")),
                "campaign_name": _clean(meta_row.get("campaign_name")),
                "adset_id": _clean(meta_row.get("adset_id")),
                "adset_name": _clean(meta_row.get("adset_name")),
                "ad_id": ad_id,
                "ad_name": _clean(meta_row.get("ad_name")),
                "spend_huf": _number_or_blank(meta_row.get("spend")),
                "impressions": _number_or_blank(meta_row.get("impressions")),
                "reach": _number_or_blank(meta_row.get("reach")),
                "clicks": _number_or_blank(meta_row.get("clicks")),
                "link_clicks": _number_or_blank(meta_row.get("link_click")),
                "landing_page_views": _number_or_blank(meta_row.get("landing_page_views")),
                "registration_leads": _number_or_blank(meta_row.get("registration_leads")),
                "exported_at": exported_at,
            }
        )
    return dedupe_ad_performance_rows(rows)


def build_lead_cohort_rows(
    *,
    daily_lead_rows: list[dict[str, Any]],
    exported_at: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    lead_counts = Counter(_clean(row.get("lead_id")) for row in daily_lead_rows if _clean(row.get("lead_id")))
    grouped: dict[str, dict[str, Any]] = {}

    for source_row in daily_lead_rows:
        lead_id = _clean(source_row.get("lead_id"))
        if not lead_id:
            continue
        row = _lead_cohort_row_from_daily_row(source_row=source_row, exported_at=exported_at)
        if lead_id not in grouped:
            grouped[lead_id] = row
            continue
        existing = grouped[lead_id]
        for column in LEAD_COHORT_COLUMNS:
            if not existing.get(column) and row.get(column):
                existing[column] = row[column]

    rows = [grouped[key] for key in sorted(grouped)]
    duplicate_leads = {lead_id: count for lead_id, count in lead_counts.items() if count > 1}
    return rows, duplicate_leads


def build_split_exports_from_daily_lead_rows(
    *,
    report_date: date,
    daily_lead_rows: list[dict[str, Any]],
    meta_data: dict[str, Any] | None,
    exported_at: str,
) -> dict[str, Any]:
    ad_rows, ad_conflicts = build_daily_ad_performance_rows(
        report_date=report_date,
        meta_data=meta_data,
        exported_at=exported_at,
    )
    if not ad_rows and daily_lead_rows:
        fallback_rows = [
            _ad_row_from_daily_lead_row(source_row=row, exported_at=exported_at)
            for row in daily_lead_rows
            if _clean(row.get("ad_id"))
        ]
        ad_rows, ad_conflicts = dedupe_ad_performance_rows(fallback_rows)

    lead_rows, duplicate_leads = build_lead_cohort_rows(
        daily_lead_rows=daily_lead_rows,
        exported_at=exported_at,
    )
    qa = build_split_export_qa(
        report_date=report_date,
        source_file="",
        source_rows=daily_lead_rows,
        ad_rows=ad_rows,
        lead_rows=lead_rows,
        duplicate_leads=duplicate_leads,
        ad_conflicts=ad_conflicts,
    )
    return {
        "ad_rows": ad_rows,
        "lead_rows": lead_rows,
        "qa": qa,
    }


def build_split_exports_from_combined_csv(
    *,
    source_path: Path,
    report_date: date,
    exported_at: str,
) -> dict[str, Any]:
    source_rows = read_csv_rows(source_path)
    fallback_rows = [
        _ad_row_from_daily_lead_row(source_row=row, exported_at=exported_at)
        for row in source_rows
        if _clean(row.get("ad_id"))
    ]
    ad_rows, ad_conflicts = dedupe_ad_performance_rows(fallback_rows)
    lead_rows, duplicate_leads = build_lead_cohort_rows(
        daily_lead_rows=source_rows,
        exported_at=exported_at,
    )
    qa = build_split_export_qa(
        report_date=report_date,
        source_file=str(source_path),
        source_rows=source_rows,
        ad_rows=ad_rows,
        lead_rows=lead_rows,
        duplicate_leads=duplicate_leads,
        ad_conflicts=ad_conflicts,
    )
    return {
        "ad_rows": ad_rows,
        "lead_rows": lead_rows,
        "qa": qa,
    }


def dedupe_ad_performance_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for row in rows:
        if _is_empty_ad_row(row):
            continue
        key = (_clean(row.get("date")), _clean(row.get("platform")), _clean(row.get("ad_id")))
        if key in grouped:
            prior = grouped[key]
            if any(_clean(prior.get(column)) != _clean(row.get(column)) for column in DAILY_AD_PERFORMANCE_COLUMNS):
                conflicts.append(
                    {
                        "date": key[0],
                        "platform": key[1],
                        "ad_id": key[2],
                    }
                )
            continue
        grouped[key] = row
    return [grouped[key] for key in sorted(grouped)], conflicts


def build_split_export_qa(
    *,
    report_date: date,
    source_file: str,
    source_rows: list[dict[str, Any]],
    ad_rows: list[dict[str, Any]],
    lead_rows: list[dict[str, Any]],
    duplicate_leads: dict[str, int],
    ad_conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    missing_required = []
    for index, row in enumerate(ad_rows, start=2):
        missing = [field for field in AD_REQUIRED_FIELDS if not _clean(row.get(field))]
        if missing:
            missing_required.append(f"ad:{index}:{'|'.join(missing)}")
    for index, row in enumerate(lead_rows, start=2):
        missing = [field for field in LEAD_REQUIRED_FIELDS if not _clean(row.get(field))]
        if missing:
            missing_required.append(f"lead:{index}:{'|'.join(missing)}")

    return {
        "date": report_date.isoformat(),
        "source_file": source_file,
        "ad_performance_file": "",
        "lead_cohort_file": "",
        "source_rows": len(source_rows),
        "unique_leads": len(lead_rows),
        "unique_date_ad_id": len({(_clean(row.get("date")), _clean(row.get("ad_id"))) for row in ad_rows}),
        "deduped_spend_huf": _sum_numeric(row.get("spend_huf") for row in ad_rows),
        "landing_page_views": _sum_numeric(row.get("landing_page_views") for row in ad_rows),
        "meta_registration_leads": _sum_numeric(row.get("registration_leads") for row in ad_rows),
        "ghl_leads": len(lead_rows),
        "bookings": sum(1 for row in lead_rows if _clean(row.get("appointment_at"))),
        "leads_without_source": sum(1 for row in lead_rows if not _clean(row.get("source"))),
        "duplicate_lead_ids": ";".join(f"{lead_id}:{count}" for lead_id, count in sorted(duplicate_leads.items())),
        "ad_data_conflicts": ";".join(
            f"{item['date']}|{item['platform']}|{item['ad_id']}" for item in ad_conflicts
        ),
        "missing_required_fields": ";".join(missing_required),
    }


def write_daily_split_csvs(
    *,
    output_dir: Path,
    report_date: date,
    ad_rows: list[dict[str, Any]],
    lead_rows: list[dict[str, Any]],
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ad_path = output_dir / f"daily_ad_performance_{report_date.isoformat()}.csv"
    lead_path = output_dir / f"lead_cohort_{report_date.isoformat()}.csv"
    write_csv(ad_path, DAILY_AD_PERFORMANCE_COLUMNS, ad_rows)
    write_csv(lead_path, LEAD_COHORT_COLUMNS, lead_rows)
    return ad_path, lead_path


def write_backfill_qa_report(path: Path, qa_rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_csv(path, BACKFILL_QA_COLUMNS, qa_rows)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _lead_cohort_row_from_daily_row(source_row: dict[str, Any], exported_at: str) -> dict[str, Any]:
    ad_id = _clean(source_row.get("ad_id"))
    attribution_status = "attributed" if ad_id and _clean(source_row.get("meta_match_level")) != "none" else "unattributed"
    return {
        "lead_id": _clean(source_row.get("lead_id") or source_row.get("contact_id")),
        "lead_created_at": _iso_value(source_row.get("created_at") or source_row.get("created_date") or source_row.get("lead_date")),
        "source": _clean(source_row.get("source")),
        "medium": "paid_social" if attribution_status == "attributed" else "",
        "attribution_status": attribution_status,
        "campaign_id": _clean(source_row.get("campaign_id")) if attribution_status == "attributed" else "",
        "campaign_name": _clean(source_row.get("campaign_name")) if attribution_status == "attributed" else "",
        "adset_id": _clean(source_row.get("adset_id")) if attribution_status == "attributed" else "",
        "adset_name": _clean(source_row.get("adset_name")) if attribution_status == "attributed" else "",
        "ad_id": ad_id if attribution_status == "attributed" else "",
        "ad_name": _clean(source_row.get("ad_name")) if attribution_status == "attributed" else "",
        "booking_created_at": _iso_value(source_row.get("booking_created_at") or source_row.get("booking_created_date")),
        "appointment_at": _iso_value(source_row.get("appointment_at") or source_row.get("booking_date")),
        "showed_at": _iso_value(source_row.get("showed_at") or source_row.get("showed_date")),
        "contract_created_at": _iso_value(source_row.get("contract_created_at") or source_row.get("contract_date")),
        "current_status": _clean(source_row.get("current_status") or source_row.get("lead_status") or source_row.get("cohort_status")),
        "exported_at": exported_at,
    }


def _ad_row_from_daily_lead_row(source_row: dict[str, Any], exported_at: str) -> dict[str, Any]:
    return {
        "date": _clean(source_row.get("date") or source_row.get("report_date") or source_row.get("lead_date"))[:10],
        "platform": "Meta",
        "campaign_id": _clean(source_row.get("campaign_id")),
        "campaign_name": _clean(source_row.get("campaign_name")),
        "adset_id": _clean(source_row.get("adset_id")),
        "adset_name": _clean(source_row.get("adset_name")),
        "ad_id": _clean(source_row.get("ad_id")),
        "ad_name": _clean(source_row.get("ad_name")),
        "spend_huf": _number_or_blank(source_row.get("spend_huf") or source_row.get("spend")),
        "impressions": _number_or_blank(source_row.get("impressions")),
        "reach": _number_or_blank(source_row.get("reach")),
        "clicks": _number_or_blank(source_row.get("clicks")),
        "link_clicks": _number_or_blank(source_row.get("link_clicks") or source_row.get("link_click")),
        "landing_page_views": _number_or_blank(source_row.get("landing_page_views")),
        "registration_leads": _number_or_blank(source_row.get("registration_leads")),
        "exported_at": exported_at,
    }


def _is_empty_ad_row(row: dict[str, Any]) -> bool:
    return bool(_clean(row.get("date"))) and not any(
        _clean(row.get(field))
        for field in DAILY_AD_PERFORMANCE_COLUMNS
        if field not in {"date", "exported_at"}
    )


def _iso_value(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    if isinstance(value, datetime):
        return _to_budapest(value).replace(microsecond=0).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return text[:10] if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-" else text
    return _to_budapest(parsed).replace(microsecond=0).isoformat()


def _to_budapest(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=BUDAPEST_TZ)
    return value.astimezone(BUDAPEST_TZ)


def _clean(value: Any) -> str:
    return "" if value in (None, "") else str(value).strip()


def _number_or_blank(value: Any) -> Any:
    text = _clean(value).replace(" Ft", "").replace("HUF", "").replace(" ", "")
    if not text:
        return ""
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        number = Decimal(text)
    except InvalidOperation:
        return text
    if number == number.to_integral_value():
        return int(number)
    return float(number)


def _sum_numeric(values: Any) -> Any:
    total = Decimal("0")
    for value in values:
        cleaned = _number_or_blank(value)
        if cleaned == "":
            continue
        total += Decimal(str(cleaned))
    if total == total.to_integral_value():
        return int(total)
    return float(total)
