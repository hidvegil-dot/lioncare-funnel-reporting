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
    build_split_exports_from_combined_csv,
    current_budapest_timestamp,
    read_csv_rows,
    write_backfill_qa_report,
    write_daily_split_csvs,
    write_schema_json,
)


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
    qa_rows = []
    missing_dates = []

    cursor = start_date
    while cursor <= end_date:
        source_path = source_files.get(cursor)
        if source_path is None:
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

        corrected_dir = source_path.parent / "corrected"
        result = build_split_exports_from_combined_csv(
            source_path=source_path,
            report_date=cursor,
            exported_at=exported_at,
            account_id=meta_ad_account_id,
        )
        ad_path, lead_path, event_path = write_daily_split_csvs(
            output_dir=corrected_dir,
            report_date=cursor,
            ad_rows=result["ad_rows"],
            lead_rows=result["lead_rows"],
            event_rows=result["event_rows"],
        )
        qa = result["qa"]
        qa["ad_performance_file"] = str(ad_path)
        qa["lead_cohort_file"] = str(lead_path)
        qa["appointment_events_file"] = str(event_path)
        qa_rows.append({field: qa.get(field, "") for field in BACKFILL_QA_COLUMNS})
        cursor += timedelta(days=1)

    qa_path = daily_root / "corrected" / "backfill_qa_report.csv"
    write_backfill_qa_report(qa_path, qa_rows)
    write_schema_json(daily_root / "corrected" / "schema.json")
    dictionary_source = Path(__file__).resolve().parents[1] / "docs" / "daily_funnel_export_data_dictionary.md"
    if dictionary_source.exists():
        (daily_root / "corrected" / "data_dictionary.md").write_text(
            dictionary_source.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

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


def _date_from_source_path(path: Path) -> date | None:
    stem = path.stem
    raw = stem.removeprefix("daily_funnel_report_")
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


if __name__ == "__main__":
    main()
