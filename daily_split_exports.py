from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


BUDAPEST_TZ = ZoneInfo("Europe/Budapest")
META_ID_RE = re.compile(r"^\d{10,25}$")

DAILY_AD_PERFORMANCE_COLUMNS = [
    "metric_date",
    "platform",
    "account_id",
    "account_timezone",
    "currency",
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
    "source_extracted_at",
    "exported_at",
]

LEAD_COHORT_COLUMNS = [
    "lead_id",
    "contact_id",
    "lead_created_at",
    "lead_created_precision",
    "source_system",
    "funnel_name",
    "landing_page_url",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "fbclid",
    "fbc",
    "fbp",
    "normalized_channel",
    "attribution_status",
    "attribution_method",
    "campaign_id",
    "campaign_name",
    "adset_id",
    "adset_name",
    "ad_id",
    "ad_name",
    "first_booking_created_at",
    "first_appointment_at",
    "latest_appointment_at",
    "showed_at",
    "no_show_at",
    "cancelled_at",
    "contract_created_at",
    "opportunity_id",
    "opportunity_created_at",
    "opportunity_updated_at",
    "opportunity_status",
    "current_status",
    "data_as_of",
    "exported_at",
]

APPOINTMENT_EVENTS_COLUMNS = [
    "event_id",
    "lead_id",
    "contact_id",
    "appointment_id",
    "event_type",
    "event_created_at",
    "appointment_start_at",
    "previous_status",
    "new_status",
    "data_as_of",
    "exported_at",
]

BACKFILL_QA_COLUMNS = [
    "date",
    "processing_status",
    "source_completeness",
    "source_file",
    "ad_performance_file",
    "lead_cohort_file",
    "appointment_events_file",
    "ad_rows",
    "unique_ads",
    "unique_leads",
    "appointment_events",
    "duplicate_ad_keys",
    "duplicate_lead_ids",
    "duplicate_event_ids",
    "exact_id_failures",
    "fake_midnight_timestamps",
    "missing_actual_timestamps",
    "meta_spend_huf",
    "landing_page_views",
    "meta_registration_leads",
    "crm_attributed_leads",
    "crm_partial_or_uncertain_leads",
    "crm_unattributed_leads",
    "bookings",
    "shows",
    "no_shows",
    "cancellations",
    "contracts",
    "attribution_coverage_pct",
    "status_timestamp_conflicts",
    "missing_required_fields",
    "conflicting_ad_snapshots",
    "notes",
]

AD_KEY_COLUMNS = ["metric_date", "platform", "account_id", "ad_id"]
NON_NEGATIVE_AD_METRICS = [
    "spend_huf",
    "impressions",
    "reach",
    "clicks",
    "link_clicks",
    "landing_page_views",
    "registration_leads",
]


class ExportValidationError(RuntimeError):
    pass


def current_budapest_timestamp() -> str:
    return datetime.now(tz=BUDAPEST_TZ).replace(microsecond=0).isoformat()


def write_schema_json(path: Path) -> None:
    schema = {
        "daily_ad_performance": _schema_for_columns(DAILY_AD_PERFORMANCE_COLUMNS, id_fields={"account_id", "campaign_id", "adset_id", "ad_id"}),
        "lead_cohort": _schema_for_columns(LEAD_COHORT_COLUMNS, id_fields={"lead_id", "contact_id", "campaign_id", "adset_id", "ad_id", "opportunity_id"}),
        "appointment_events": _schema_for_columns(APPOINTMENT_EVENTS_COLUMNS, id_fields={"event_id", "lead_id", "contact_id", "appointment_id"}),
        "primary_keys": {
            "daily_ad_performance": AD_KEY_COLUMNS,
            "lead_cohort": ["lead_id"],
            "appointment_events": ["event_id"],
        },
        "timezone": "Europe/Budapest",
        "id_policy": "Meta and CRM identifiers are strings and must never be parsed as numbers.",
    }
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")


def build_daily_ad_performance_rows(
    *,
    report_date: date,
    meta_data: dict[str, Any] | None,
    exported_at: str,
    account_id: str = "",
    account_timezone: str = "Europe/Budapest",
    currency: str = "HUF",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = []
    source_extracted_at = _clean((meta_data or {}).get("source_extracted_at")) or exported_at
    effective_account_id = _clean((meta_data or {}).get("account_id")) or _strip_act_prefix(account_id)
    for meta_row in (meta_data or {}).get("ads") or []:
        ad_id = _id_string(meta_row.get("ad_id"))
        if not ad_id:
            continue
        rows.append(
            {
                "metric_date": _clean(meta_row.get("date_start")) or report_date.isoformat(),
                "platform": "Meta",
                "account_id": effective_account_id,
                "account_timezone": account_timezone,
                "currency": currency,
                "campaign_id": _id_string(meta_row.get("campaign_id")),
                "campaign_name": _clean(meta_row.get("campaign_name")),
                "adset_id": _id_string(meta_row.get("adset_id")),
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
                "source_extracted_at": source_extracted_at,
                "exported_at": exported_at,
            }
        )
    rows, conflicts = dedupe_ad_performance_rows(rows)
    validate_ad_rows(rows)
    return rows, conflicts


def build_split_exports_from_daily_lead_rows(
    *,
    report_date: date,
    daily_lead_rows: list[dict[str, Any]],
    meta_data: dict[str, Any] | None,
    exported_at: str,
    appointments: list[dict[str, Any]] | None = None,
    account_id: str = "",
) -> dict[str, Any]:
    ad_rows, ad_conflicts = build_daily_ad_performance_rows(
        report_date=report_date,
        meta_data=meta_data,
        exported_at=exported_at,
        account_id=account_id,
    )
    lead_rows, duplicate_leads = build_lead_cohort_rows(
        daily_lead_rows=daily_lead_rows,
        exported_at=exported_at,
        data_as_of=exported_at,
    )
    event_rows = build_appointment_event_rows(
        appointments=appointments or [],
        lead_rows=lead_rows,
        exported_at=exported_at,
        data_as_of=exported_at,
    )
    validate_event_consistency(lead_rows=lead_rows, event_rows=event_rows)
    qa = build_split_export_qa(
        report_date=report_date,
        source_file="api:meta+ghl",
        processing_status="generated",
        source_completeness="api",
        source_rows=daily_lead_rows,
        ad_rows=ad_rows,
        lead_rows=lead_rows,
        event_rows=event_rows,
        duplicate_leads=duplicate_leads,
        ad_conflicts=ad_conflicts,
    )
    return {"ad_rows": ad_rows, "lead_rows": lead_rows, "event_rows": event_rows, "qa": qa}


def build_split_exports_from_combined_csv(
    *,
    source_path: Path,
    report_date: date,
    exported_at: str,
    account_id: str = "",
) -> dict[str, Any]:
    source_rows = read_csv_rows(source_path)
    ad_rows, ad_conflicts = dedupe_ad_performance_rows(
        [
            _ad_row_from_legacy_daily_lead_row(row, exported_at=exported_at, account_id=account_id)
            for row in source_rows
            if _clean(row.get("ad_id"))
        ]
    )
    validate_ad_rows(ad_rows)
    lead_rows, duplicate_leads = build_lead_cohort_rows(
        daily_lead_rows=source_rows,
        exported_at=exported_at,
        data_as_of=exported_at,
    )
    qa = build_split_export_qa(
        report_date=report_date,
        source_file=str(source_path),
        processing_status="generated_from_legacy_lead_csv",
        source_completeness="partial_legacy_csv",
        source_rows=source_rows,
        ad_rows=ad_rows,
        lead_rows=lead_rows,
        event_rows=[],
        duplicate_leads=duplicate_leads,
        ad_conflicts=ad_conflicts,
    )
    return {"ad_rows": ad_rows, "lead_rows": lead_rows, "event_rows": [], "qa": qa}


def build_lead_cohort_rows(
    *,
    daily_lead_rows: list[dict[str, Any]],
    exported_at: str,
    data_as_of: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    counts = Counter(_clean(row.get("lead_id") or row.get("contact_id")) for row in daily_lead_rows if _clean(row.get("lead_id") or row.get("contact_id")))
    grouped: dict[str, dict[str, Any]] = {}
    for source_row in daily_lead_rows:
        lead_id = _clean(source_row.get("lead_id") or source_row.get("contact_id"))
        if not lead_id:
            continue
        row = _lead_cohort_row_from_daily_row(source_row, exported_at=exported_at, data_as_of=data_as_of)
        if lead_id not in grouped:
            grouped[lead_id] = row
            continue
        for column in LEAD_COHORT_COLUMNS:
            if not grouped[lead_id].get(column) and row.get(column):
                grouped[lead_id][column] = row[column]
    rows = [grouped[key] for key in sorted(grouped)]
    validate_lead_rows(rows)
    return rows, {key: count for key, count in counts.items() if count > 1}


def build_appointment_event_rows(
    *,
    appointments: list[dict[str, Any]],
    lead_rows: list[dict[str, Any]],
    exported_at: str,
    data_as_of: str,
) -> list[dict[str, Any]]:
    lead_by_contact = {_clean(row.get("contact_id")): _clean(row.get("lead_id")) for row in lead_rows}
    events = []
    for appointment in appointments:
        contact_id = _appointment_contact_id(appointment)
        lead_id = lead_by_contact.get(contact_id, contact_id)
        appointment_id = _clean(appointment.get("id") or appointment.get("_id"))
        start_at, _ = _value_and_precision(appointment.get("startTime") or appointment.get("date"))
        created_at, _ = _value_and_precision(appointment.get("dateAdded") or appointment.get("createdAt"))
        status = _clean(
            appointment.get("appointmentStatus")
            or appointment.get("status")
            or appointment.get("calendarStatus")
            or appointment.get("appoinmentStatus")
        ).lower()
        event_type = _appointment_event_type(status)
        if not event_type:
            continue
        events.append(
            {
                "event_id": f"{appointment_id or contact_id}:{event_type}:{created_at or start_at}",
                "lead_id": lead_id,
                "contact_id": contact_id,
                "appointment_id": appointment_id,
                "event_type": event_type,
                "event_created_at": created_at,
                "appointment_start_at": start_at,
                "previous_status": "",
                "new_status": status,
                "data_as_of": data_as_of,
                "exported_at": exported_at,
            }
        )
    return sorted(events, key=lambda row: row["event_id"])


def dedupe_ad_performance_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    conflicts = []
    for row in rows:
        if _is_empty_ad_row(row):
            continue
        key = tuple(_clean(row.get(column)) for column in AD_KEY_COLUMNS)
        if key in grouped:
            prior = grouped[key]
            if _clean(row.get("source_extracted_at")) > _clean(prior.get("source_extracted_at")):
                conflicts.append({"key": "|".join(key), "selected": "newer_source_extracted_at"})
                grouped[key] = row
            elif any(_clean(prior.get(column)) != _clean(row.get(column)) for column in DAILY_AD_PERFORMANCE_COLUMNS):
                conflicts.append({"key": "|".join(key), "selected": "first_snapshot"})
            continue
        grouped[key] = row
    return [grouped[key] for key in sorted(grouped)], conflicts


def build_split_export_qa(
    *,
    report_date: date,
    source_file: str,
    processing_status: str,
    source_completeness: str,
    source_rows: list[dict[str, Any]],
    ad_rows: list[dict[str, Any]],
    lead_rows: list[dict[str, Any]],
    event_rows: list[dict[str, Any]],
    duplicate_leads: dict[str, int],
    ad_conflicts: list[dict[str, Any]],
    notes: str = "",
) -> dict[str, Any]:
    fake_midnight = _fake_midnight_count(lead_rows + event_rows)
    missing_required = _missing_required_fields(ad_rows, lead_rows, event_rows)
    attributed = sum(1 for row in lead_rows if row.get("attribution_status") == "attributed")
    partial_or_uncertain = sum(1 for row in lead_rows if row.get("attribution_status") in {"partial", "uncertain"})
    unattributed = sum(1 for row in lead_rows if row.get("attribution_status") == "unattributed")
    bookings = sum(1 for row in lead_rows if _clean(row.get("first_appointment_at")))
    shows = sum(1 for row in lead_rows if _clean(row.get("showed_at")))
    no_shows = sum(1 for row in lead_rows if _clean(row.get("no_show_at")))
    cancellations = sum(1 for row in lead_rows if _clean(row.get("cancelled_at")))
    contracts = sum(1 for row in lead_rows if _clean(row.get("contract_created_at")))
    return {
        "date": report_date.isoformat(),
        "processing_status": processing_status,
        "source_completeness": source_completeness,
        "source_file": source_file,
        "ad_performance_file": "",
        "lead_cohort_file": "",
        "appointment_events_file": "",
        "ad_rows": len(ad_rows),
        "unique_ads": len({tuple(_clean(row.get(column)) for column in AD_KEY_COLUMNS) for row in ad_rows}),
        "unique_leads": len(lead_rows),
        "appointment_events": len(event_rows),
        "duplicate_ad_keys": _duplicate_ad_key_count(ad_rows),
        "duplicate_lead_ids": ";".join(f"{lead_id}:{count}" for lead_id, count in sorted(duplicate_leads.items())),
        "duplicate_event_ids": _duplicate_event_id_count(event_rows),
        "exact_id_failures": ";".join(_id_failures(ad_rows, lead_rows)),
        "fake_midnight_timestamps": fake_midnight,
        "missing_actual_timestamps": _missing_actual_timestamp_notes(lead_rows),
        "meta_spend_huf": _sum_numeric(row.get("spend_huf") for row in ad_rows),
        "landing_page_views": _sum_numeric(row.get("landing_page_views") for row in ad_rows),
        "meta_registration_leads": _sum_numeric(row.get("registration_leads") for row in ad_rows),
        "crm_attributed_leads": attributed,
        "crm_partial_or_uncertain_leads": partial_or_uncertain,
        "crm_unattributed_leads": unattributed,
        "bookings": bookings,
        "shows": shows,
        "no_shows": no_shows,
        "cancellations": cancellations,
        "contracts": contracts,
        "attribution_coverage_pct": round((attributed / len(lead_rows)) * 100, 2) if lead_rows else "",
        "status_timestamp_conflicts": ";".join(_status_timestamp_conflicts(lead_rows, event_rows)),
        "missing_required_fields": ";".join(missing_required),
        "conflicting_ad_snapshots": ";".join(str(item) for item in ad_conflicts),
        "notes": notes,
    }


def validate_ad_rows(rows: list[dict[str, Any]]) -> None:
    keys = [tuple(_clean(row.get(column)) for column in AD_KEY_COLUMNS) for row in rows]
    if len(keys) != len(set(keys)):
        raise ExportValidationError("Duplicate daily_ad_performance primary key remains")
    for row in rows:
        for field in ("account_id", "campaign_id", "adset_id", "ad_id"):
            value = _clean(row.get(field))
            if value and not META_ID_RE.fullmatch(value):
                raise ExportValidationError(f"Invalid or damaged Meta ID in {field}: {value}")
        for field in NON_NEGATIVE_AD_METRICS:
            value = row.get(field)
            if value != "" and Decimal(str(value)) < 0:
                raise ExportValidationError(f"Negative ad metric {field}: {value}")


def validate_lead_rows(rows: list[dict[str, Any]]) -> None:
    ids = [_clean(row.get("lead_id")) for row in rows]
    if len(ids) != len(set(ids)):
        raise ExportValidationError("Duplicate lead_id remains")
    fake_midnight = _fake_midnight_count(rows)
    if fake_midnight:
        raise ExportValidationError(f"Fake midnight timestamps detected: {fake_midnight}")


def validate_event_consistency(*, lead_rows: list[dict[str, Any]], event_rows: list[dict[str, Any]]) -> None:
    event_ids = [_clean(row.get("event_id")) for row in event_rows]
    if len(event_ids) != len(set(event_ids)):
        raise ExportValidationError("Duplicate appointment event_id remains")
    events_by_lead: dict[str, set[str]] = {}
    for event in event_rows:
        events_by_lead.setdefault(_clean(event.get("lead_id")), set()).add(_clean(event.get("event_type")))
    for lead in lead_rows:
        lead_id = _clean(lead.get("lead_id"))
        event_types = events_by_lead.get(lead_id, set())
        if _clean(lead.get("first_appointment_at")) and "booking_created" not in event_types:
            raise ExportValidationError(f"Lead has appointment but no booking event: {lead_id}")
        status = _clean(lead.get("current_status")).lower()
        if "show" in status and "showed" not in event_types and not _clean(lead.get("showed_at")):
            raise ExportValidationError(f"Lead status showed conflicts with event history: {lead_id}")


def write_daily_split_csvs(
    *,
    output_dir: Path,
    report_date: date,
    ad_rows: list[dict[str, Any]],
    lead_rows: list[dict[str, Any]],
    event_rows: list[dict[str, Any]] | None = None,
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ad_path = output_dir / f"daily_ad_performance_{report_date.isoformat()}.csv"
    lead_path = output_dir / f"lead_cohort_{report_date.isoformat()}.csv"
    event_path = output_dir / f"appointment_events_{report_date.isoformat()}.csv"
    write_csv(ad_path, DAILY_AD_PERFORMANCE_COLUMNS, ad_rows)
    write_csv(lead_path, LEAD_COHORT_COLUMNS, lead_rows)
    write_csv(event_path, APPOINTMENT_EVENTS_COLUMNS, event_rows or [])
    return ad_path, lead_path, event_path


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


def _lead_cohort_row_from_daily_row(source_row: dict[str, Any], *, exported_at: str, data_as_of: str) -> dict[str, Any]:
    ad_id = _id_string(source_row.get("ad_id"))
    has_meta_id = bool(ad_id and _clean(source_row.get("meta_match_level")) != "none")
    attribution_status = "attributed" if has_meta_id else "unattributed"
    lead_created_at, lead_precision = _value_and_precision(
        source_row.get("created_at") or source_row.get("created_date") or source_row.get("lead_date")
    )
    first_booking_created_at, _ = _value_and_precision(
        source_row.get("booking_created_at") or source_row.get("booking_created_date")
    )
    first_appointment_at, _ = _value_and_precision(
        source_row.get("appointment_at") or source_row.get("booking_date")
    )
    showed_at, _ = _value_and_precision(source_row.get("showed_at") or source_row.get("showed_date"))
    contract_created_at, _ = _value_and_precision(
        source_row.get("contract_created_at") or source_row.get("contract_date")
    )
    opportunity_created_at, _ = _value_and_precision(source_row.get("opportunity_created_at"))
    opportunity_updated_at, _ = _value_and_precision(source_row.get("opportunity_updated_at"))
    contact_id = _clean(source_row.get("contact_id") or source_row.get("lead_id"))
    raw_lead_id = _clean(source_row.get("form_submission_id") or source_row.get("lead_event_id") or source_row.get("lead_id"))
    return {
        "lead_id": raw_lead_id or contact_id,
        "contact_id": contact_id,
        "lead_created_at": lead_created_at,
        "lead_created_precision": lead_precision,
        "source_system": "GHL",
        "funnel_name": _clean(source_row.get("source")),
        "landing_page_url": _clean(source_row.get("landing_page_url")),
        "utm_source": _clean(source_row.get("utm_source")),
        "utm_medium": _clean(source_row.get("utm_medium")),
        "utm_campaign": _clean(source_row.get("utm_campaign") or source_row.get("campaign_name")),
        "fbclid": _clean(source_row.get("fbclid")),
        "fbc": _clean(source_row.get("fbc")),
        "fbp": _clean(source_row.get("fbp")),
        "normalized_channel": "paid_social" if attribution_status == "attributed" else "",
        "attribution_status": attribution_status,
        "attribution_method": "meta_ad_id" if attribution_status == "attributed" else "",
        "campaign_id": _id_string(source_row.get("campaign_id")) if attribution_status == "attributed" else "",
        "campaign_name": _clean(source_row.get("campaign_name")) if attribution_status == "attributed" else "",
        "adset_id": _id_string(source_row.get("adset_id")) if attribution_status == "attributed" else "",
        "adset_name": _clean(source_row.get("adset_name")) if attribution_status == "attributed" else "",
        "ad_id": ad_id if attribution_status == "attributed" else "",
        "ad_name": _clean(source_row.get("ad_name")) if attribution_status == "attributed" else "",
        "first_booking_created_at": first_booking_created_at,
        "first_appointment_at": first_appointment_at,
        "latest_appointment_at": first_appointment_at,
        "showed_at": showed_at,
        "no_show_at": "",
        "cancelled_at": "",
        "contract_created_at": contract_created_at,
        "opportunity_id": _clean(source_row.get("opportunity_id")),
        "opportunity_created_at": opportunity_created_at,
        "opportunity_updated_at": opportunity_updated_at,
        "opportunity_status": _clean(source_row.get("opportunity_status")),
        "current_status": _clean(source_row.get("lead_status") or source_row.get("cohort_status")),
        "data_as_of": data_as_of,
        "exported_at": exported_at,
    }


def _ad_row_from_legacy_daily_lead_row(source_row: dict[str, Any], *, exported_at: str, account_id: str = "") -> dict[str, Any]:
    return {
        "metric_date": _clean(source_row.get("metric_date") or source_row.get("date") or source_row.get("report_date") or source_row.get("lead_date"))[:10],
        "platform": "Meta",
        "account_id": _id_string(source_row.get("account_id") or account_id),
        "account_timezone": "Europe/Budapest",
        "currency": "HUF",
        "campaign_id": _id_string(source_row.get("campaign_id")),
        "campaign_name": _clean(source_row.get("campaign_name")),
        "adset_id": _id_string(source_row.get("adset_id")),
        "adset_name": _clean(source_row.get("adset_name")),
        "ad_id": _id_string(source_row.get("ad_id")),
        "ad_name": _clean(source_row.get("ad_name")),
        "spend_huf": _number_or_blank(source_row.get("spend_huf") or source_row.get("spend")),
        "impressions": _number_or_blank(source_row.get("impressions")),
        "reach": _number_or_blank(source_row.get("reach")),
        "clicks": _number_or_blank(source_row.get("clicks")),
        "link_clicks": _number_or_blank(source_row.get("link_clicks") or source_row.get("link_click")),
        "landing_page_views": _number_or_blank(source_row.get("landing_page_views")),
        "registration_leads": _number_or_blank(source_row.get("registration_leads")),
        "source_extracted_at": _clean(source_row.get("source_extracted_at")) or exported_at,
        "exported_at": exported_at,
    }


def _value_and_precision(value: Any) -> tuple[str, str]:
    text = _clean(value)
    if not text:
        return "", ""
    if isinstance(value, datetime):
        return _to_budapest(value).replace(microsecond=0).isoformat(), "datetime"
    if isinstance(value, date):
        return value.isoformat(), "date"
    normalized = text.replace("Z", "+00:00")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
        return normalized, "date"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return text, ""
    if parsed.time().hour == 0 and parsed.time().minute == 0 and parsed.time().second == 0 and "T00:00:00" in normalized:
        return parsed.date().isoformat(), "date"
    return _to_budapest(parsed).replace(microsecond=0).isoformat(), "datetime"


def _is_empty_ad_row(row: dict[str, Any]) -> bool:
    return bool(_clean(row.get("metric_date"))) and not any(
        _clean(row.get(field))
        for field in DAILY_AD_PERFORMANCE_COLUMNS
        if field not in {"metric_date", "source_extracted_at", "exported_at"}
    )


def _schema_for_columns(columns: list[str], *, id_fields: set[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            column: {
                "type": "string" if column in id_fields or column.endswith("_at") or column.endswith("_date") or column.endswith("_precision") else ["string", "number"]
            }
            for column in columns
        },
        "required": columns,
    }


def _missing_required_fields(ad_rows: list[dict[str, Any]], lead_rows: list[dict[str, Any]], event_rows: list[dict[str, Any]]) -> list[str]:
    missing = []
    for prefix, rows, columns in (
        ("ad", ad_rows, ["metric_date", "platform", "account_id", "ad_id"]),
        ("lead", lead_rows, ["lead_id", "contact_id", "lead_created_at", "source_system", "attribution_status", "current_status"]),
        ("event", event_rows, ["event_id", "lead_id", "contact_id", "event_type"]),
    ):
        for index, row in enumerate(rows, start=2):
            fields = [column for column in columns if not _clean(row.get(column))]
            if fields:
                missing.append(f"{prefix}:{index}:{'|'.join(fields)}")
    return missing


def _status_timestamp_conflicts(lead_rows: list[dict[str, Any]], event_rows: list[dict[str, Any]]) -> list[str]:
    conflicts = []
    event_types_by_lead: dict[str, set[str]] = {}
    for event in event_rows:
        event_types_by_lead.setdefault(_clean(event.get("lead_id")), set()).add(_clean(event.get("event_type")))
    for row in lead_rows:
        lead_id = _clean(row.get("lead_id"))
        status = _clean(row.get("current_status")).lower()
        if _clean(row.get("first_appointment_at")) and "booking_created" not in event_types_by_lead.get(lead_id, set()):
            conflicts.append(f"{lead_id}:appointment_without_event")
        if "book" in status and not _clean(row.get("first_appointment_at")):
            conflicts.append(f"{lead_id}:booked_without_appointment")
        if "show" in status and "showed" not in event_types_by_lead.get(lead_id, set()) and not _clean(row.get("showed_at")):
            conflicts.append(f"{lead_id}:showed_without_event")
        if "cancel" in status and "cancelled" not in event_types_by_lead.get(lead_id, set()):
            conflicts.append(f"{lead_id}:cancelled_without_event")
    return conflicts


def _missing_actual_timestamp_notes(lead_rows: list[dict[str, Any]]) -> str:
    count = sum(1 for row in lead_rows if row.get("lead_created_precision") == "date")
    return f"lead_created_at_date_precision:{count}" if count else ""


def _fake_midnight_count(rows: list[dict[str, Any]]) -> int:
    total = 0
    for row in rows:
        for key, value in row.items():
            if key.endswith("_at") and isinstance(value, str) and "T00:00:00" in value:
                total += 1
    return total


def _duplicate_ad_key_count(rows: list[dict[str, Any]]) -> int:
    keys = [tuple(_clean(row.get(column)) for column in AD_KEY_COLUMNS) for row in rows]
    return len(keys) - len(set(keys))


def _duplicate_event_id_count(rows: list[dict[str, Any]]) -> int:
    event_ids = [_clean(row.get("event_id")) for row in rows]
    return len(event_ids) - len(set(event_ids))


def _id_failures(ad_rows: list[dict[str, Any]], lead_rows: list[dict[str, Any]]) -> list[str]:
    failures = []
    for source, rows in (("ad", ad_rows), ("lead", lead_rows)):
        for index, row in enumerate(rows, start=2):
            for field in ("account_id", "campaign_id", "adset_id", "ad_id"):
                value = _clean(row.get(field))
                if value and not META_ID_RE.fullmatch(value):
                    failures.append(f"{source}:{index}:{field}:{value}")
    return failures


def _appointment_contact_id(appointment: dict[str, Any]) -> str:
    for key in ("contactId", "contact_id", "appointmentContactId"):
        if _clean(appointment.get(key)):
            return _clean(appointment.get(key))
    contact = appointment.get("contact")
    if isinstance(contact, dict):
        return _clean(contact.get("id") or contact.get("_id"))
    return ""


def _appointment_event_type(status: str) -> str:
    if status in {"showed", "show", "completed", "attended", "confirmed-show"}:
        return "showed"
    if status in {"noshow", "no-show", "no_show", "did_not_show"}:
        return "no_show"
    if status in {"cancelled", "canceled", "cancelled_by_user", "canceled_by_user"}:
        return "cancelled"
    if status:
        return "booking_created"
    return ""


def _to_budapest(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=BUDAPEST_TZ)
    return value.astimezone(BUDAPEST_TZ)


def _strip_act_prefix(value: Any) -> str:
    return _clean(value).removeprefix("act_")


def _id_string(value: Any) -> str:
    text = _strip_act_prefix(value)
    if not text:
        return ""
    if "e+" in text.lower() or "." in text:
        raise ExportValidationError(f"Possible damaged numeric ID: {text}")
    return text


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
