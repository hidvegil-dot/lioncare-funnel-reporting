from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from daily_split_exports import (
    BACKFILL_QA_COLUMNS,
    ExportValidationError,
    build_split_exports_from_combined_csv,
    current_budapest_timestamp,
    read_csv_rows,
    write_backfill_qa_report,
    write_daily_split_csvs,
    write_schema_json,
)
from ghl_client import GHLAPIError, GHLClient, GHLConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Regenerate corrected daily ad performance and lead cohort CSVs from daily source CSV files."
    )
    parser.add_argument(
        "--daily-root",
        required=True,
        help="Root daily report folder, for example .../LionCare/riport/daily.",
    )
    parser.add_argument(
        "--start-date",
        default="2026-08-01",
        help="Inclusive start date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--end-date",
        default="",
        help="Inclusive end date in YYYY-MM-DD format. Defaults to the latest available source day.",
    )
    parser.add_argument(
        "--meta-ad-account-id",
        default="",
        help="Meta ad account ID used when legacy source CSV lacks account_id.",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    daily_root = Path(args.daily_root).expanduser().resolve()
    start_date = date.fromisoformat(args.start_date)
    source_files = find_source_files(daily_root=daily_root, start_date=start_date)
    if not source_files:
        raise SystemExit(f"No daily source CSV files found from {start_date.isoformat()} under {daily_root}")

    available_dates = sorted(source_files)
    end_date = date.fromisoformat(args.end_date) if args.end_date else available_dates[-1]
    exported_at = current_budapest_timestamp()
    meta_ad_account_id = (args.meta_ad_account_id or os.getenv("META_AD_ACCOUNT_ID", "")).strip().removeprefix("act_")
    ghl_client = _build_ghl_client_optional()
    corrected_root = daily_root / "corrected" / "2026-08"
    qa_rows = []
    missing_dates = []

    cursor = start_date
    while cursor <= end_date:
        source_path = source_files.get(cursor)
        if source_path is None:
            _remove_stale_corrected_outputs(corrected_root, cursor)
            missing_dates.append(cursor.isoformat())
            qa_rows.append(
                {
                    "date": cursor.isoformat(),
                    "processing_status": "insufficient_source",
                    "source_completeness": "missing_source",
                    "notes": "No source CSV found; no synthetic output generated.",
                }
            )
            cursor += timedelta(days=1)
            continue

        source_rows = read_csv_rows(source_path)
        missing_source_columns = _missing_source_columns(source_rows)
        if missing_source_columns:
            _remove_stale_corrected_outputs(corrected_root, cursor)
            qa_rows.append(
                {
                    "date": cursor.isoformat(),
                    "processing_status": "insufficient_source",
                    "source_completeness": "aggregate_only",
                    "source_file": str(source_path),
                    "source_rows": len(source_rows),
                    "missing_required_fields": f"incompatible_source:{'|'.join(missing_source_columns)}",
                    "notes": "Source file is aggregate daily report, not raw lead/ad records; no corrected output generated.",
                }
            )
            cursor += timedelta(days=1)
            continue

        appointments = []
        appointment_notes = ""
        if _requires_appointment_source(source_rows):
            if ghl_client is None:
                _remove_stale_corrected_outputs(corrected_root, cursor)
                qa_rows.append(
                    {
                        "date": cursor.isoformat(),
                        "processing_status": "insufficient_source",
                        "source_completeness": "missing_ghl_api_for_appointments",
                        "source_file": str(source_path),
                        "missing_required_fields": "appointment_source:GHL_API",
                        "notes": "Source rows contain booking/show/cancel status but no live GHL client was available; no synthetic appointment events generated.",
                    }
                )
                cursor += timedelta(days=1)
                continue
            try:
                appointments = _fetch_appointments_for_source_rows(ghl_client, source_rows)
                appointment_notes = f"appointment_source=ghl_contact_appointments;appointments={len(appointments)}"
            except GHLAPIError as exc:
                _remove_stale_corrected_outputs(corrected_root, cursor)
                qa_rows.append(
                    {
                        "date": cursor.isoformat(),
                        "processing_status": "insufficient_source",
                        "source_completeness": "ghl_appointment_api_failed",
                        "source_file": str(source_path),
                        "missing_required_fields": "appointment_source:GHL_API",
                        "notes": f"Could not fetch GHL appointments; no synthetic appointment events generated. {exc}",
                    }
                )
                cursor += timedelta(days=1)
                continue

        try:
            result = build_split_exports_from_combined_csv(
                source_path=source_path,
                report_date=cursor,
                exported_at=exported_at,
                account_id=meta_ad_account_id,
                appointments=appointments,
                require_event_consistency=bool(appointments or _requires_appointment_source(source_rows)),
            )
        except ExportValidationError as exc:
            _remove_stale_corrected_outputs(corrected_root, cursor)
            qa_rows.append(
                {
                    "date": cursor.isoformat(),
                    "processing_status": "insufficient_source",
                    "source_completeness": "appointment_event_consistency_failed",
                    "source_file": str(source_path),
                    "missing_required_fields": "appointment_events",
                    "notes": f"{appointment_notes}; validation_error={exc}",
                }
            )
            cursor += timedelta(days=1)
            continue
        ad_path, lead_path, event_path = write_daily_split_csvs(
            output_dir=corrected_root,
            report_date=cursor,
            ad_rows=result["ad_rows"],
            lead_rows=result["lead_rows"],
            event_rows=result["event_rows"],
        )
        qa = result["qa"]
        qa["ad_performance_file"] = str(ad_path)
        qa["lead_cohort_file"] = str(lead_path)
        qa["appointment_events_file"] = str(event_path)
        qa["notes"] = "; ".join(part for part in [str(qa.get("notes") or ""), appointment_notes] if part)
        qa_rows.append({field: qa.get(field, "") for field in BACKFILL_QA_COLUMNS})
        cursor += timedelta(days=1)

    qa_path = corrected_root / "backfill_qa_report.csv"
    write_backfill_qa_report(qa_path, qa_rows)
    write_schema_json(corrected_root / "schema.json")
    dictionary_source = Path(__file__).resolve().parents[1] / "docs" / "daily_funnel_export_data_dictionary.md"
    if dictionary_source.exists():
        (corrected_root / "data_dictionary.md").write_text(
            dictionary_source.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    source_mapping = Path(__file__).resolve().parents[1] / "docs" / "daily_funnel_source_mapping.md"
    if source_mapping.exists():
        (corrected_root / "source_mapping.md").write_text(
            source_mapping.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    _write_august_summary(corrected_root / "august_backfill_summary.md", qa_rows)

    print(f"Processed dates: {', '.join(row['date'] for row in qa_rows)}")
    print(f"Missing dates: {', '.join(missing_dates) if missing_dates else 'none'}")
    print(f"QA report: {qa_path}")


def find_source_files(*, daily_root: Path, start_date: date) -> dict[date, Path]:
    source_files: dict[date, Path] = {}
    for path in daily_root.glob("2026/08/2026-08-*/daily_funnel_report_2026-08-*.csv"):
        report_date = _date_from_source_path(path)
        if report_date and report_date >= start_date:
            source_files[report_date] = path
    return source_files


def _missing_source_columns(rows: list[dict[str, str]]) -> list[str]:
    required = {"report_date", "lead_id", "created_date", "source", "ad_id", "meta_match_level"}
    columns = set(rows[0].keys()) if rows else set()
    return sorted(required - columns)


def _build_ghl_client_optional() -> GHLClient | None:
    try:
        return GHLClient(GHLConfig.from_env())
    except Exception:
        return None


def _requires_appointment_source(rows: list[dict[str, str]]) -> bool:
    for row in rows:
        status = str(row.get("lead_status") or row.get("cohort_status") or "").strip().lower()
        if any(token in status for token in ("book", "future_booking", "show", "no_show", "cancel")):
            return True
        if any(str(row.get(key) or "").strip() for key in ("booking_date", "booking_created_at", "appointment_at", "showed_at", "no_show_at", "cancelled_at")):
            return True
    return False


def _fetch_appointments_for_source_rows(client: GHLClient, rows: list[dict[str, str]]) -> list[dict[str, object]]:
    appointments: list[dict[str, object]] = []
    contact_ids = sorted(
        {
            str(row.get("contact_id") or row.get("lead_id") or "").strip()
            for row in rows
            if str(row.get("contact_id") or row.get("lead_id") or "").strip()
        }
    )
    for contact_id in contact_ids:
        for appointment in client.fetch_contact_appointments(contact_id):
            if isinstance(appointment, dict):
                appointment.setdefault("contactId", contact_id)
                appointments.append(appointment)
    return appointments


def _remove_stale_corrected_outputs(corrected_root: Path, report_date: date) -> None:
    for prefix in ("daily_ad_performance", "lead_cohort", "appointment_events"):
        path = corrected_root / f"{prefix}_{report_date.isoformat()}.csv"
        if path.exists():
            path.unlink()


def _date_from_source_path(path: Path) -> date | None:
    stem = path.stem
    raw = stem.removeprefix("daily_funnel_report_")
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _write_august_summary(path: Path, qa_rows: list[dict[str, object]]) -> None:
    generated = [str(row.get("date")) for row in qa_rows if str(row.get("processing_status")) not in {"insufficient_source", ""}]
    insufficient = [str(row.get("date")) for row in qa_rows if row.get("processing_status") == "insufficient_source"]
    lines = [
        "# August 2026 Corrected Backfill Summary",
        "",
        f"Generated dates: {', '.join(generated) if generated else 'none'}",
        f"Insufficient source dates: {', '.join(insufficient) if insufficient else 'none'}",
        "",
        "This backfill is idempotent for the same source files and writes only into corrected/2026-08.",
        "Aggregate-only source days are documented in QA and do not receive synthetic output files.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
