from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daily_split_exports import (
    APPOINTMENT_EVENTS_COLUMNS,
    current_budapest_timestamp,
    read_csv_rows,
    repair_legacy_appointment_event_rows,
    validate_appointment_event_rows,
    write_csv,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Conservatively regenerate one daily appointment event CSV from a legacy export."
    )
    parser.add_argument("--source", required=True, help="Legacy appointment_events CSV path.")
    parser.add_argument("--report-date", required=True, help="Daily partition in YYYY-MM-DD format.")
    parser.add_argument("--output", default="", help="Output CSV. Defaults to replacing --source.")
    parser.add_argument(
        "--historical-daily-root",
        default="",
        help="Daily report root containing GHL-derived daily_funnel_report_*.csv snapshots.",
    )
    parser.add_argument("--qa-output", default="", help="QA JSON path. Defaults next to the output CSV.")
    parser.add_argument(
        "--missing-source-note",
        action="append",
        default=[],
        help="Document an unavailable source, for example a failed live GHL API read.",
    )
    parser.add_argument(
        "--exported-at",
        default="",
        help="ISO 8601 export timestamp. Defaults to the current Europe/Budapest timestamp.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report_date = date.fromisoformat(args.report_date)
    source_path = Path(args.source).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve() if args.output else source_path
    qa_path = (
        Path(args.qa_output).expanduser().resolve()
        if args.qa_output
        else output_path.with_name(f"{output_path.stem}.qa.json")
    )
    backup_path = output_path.with_name(f"{output_path.stem}.before_repair.csv")
    legacy_input_path = backup_path if source_path == output_path and backup_path.exists() else source_path
    legacy_rows = read_csv_rows(legacy_input_path)
    target_contact_ids = {
        str(row.get("contact_id") or "").strip()
        for row in legacy_rows
        if str(row.get("contact_id") or "").strip()
    }
    historical_rows, scanned_files = _load_historical_opportunity_links(
        daily_root=Path(args.historical_daily_root).expanduser().resolve()
        if args.historical_daily_root
        else None,
        target_contact_ids=target_contact_ids,
    )
    exported_at = args.exported_at or current_budapest_timestamp()
    repaired_rows, audit = repair_legacy_appointment_event_rows(
        report_date=report_date,
        legacy_rows=legacy_rows,
        historical_lead_rows=historical_rows,
        exported_at=exported_at,
    )
    validate_appointment_event_rows(repaired_rows, report_date=report_date)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path == output_path:
        if not backup_path.exists():
            shutil.copy2(source_path, backup_path)
        audit["backup_path"] = str(backup_path)
    temp_path = output_path.with_name(f".{output_path.name}.tmp")
    write_csv(temp_path, APPOINTMENT_EVENTS_COLUMNS, repaired_rows)
    temp_path.replace(output_path)

    audit.update(
        {
            "source_path": str(source_path),
            "legacy_input_path": str(legacy_input_path),
            "output_path": str(output_path),
            "qa_path": str(qa_path),
            "historical_snapshot_files_scanned": scanned_files,
            "exported_at": exported_at,
            "required_columns": APPOINTMENT_EVENTS_COLUMNS,
        }
    )
    audit["missing_source_data"].extend(args.missing_source_note)
    qa_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"kept_events={audit['kept_events']}")
    print(f"removed_other_date_records={audit['removed_other_date_records']}")
    print(f"unlinked_appointment_events={audit['unlinked_appointment_events']}")
    print(f"output={output_path}")
    print(f"qa={qa_path}")


def _load_historical_opportunity_links(
    *,
    daily_root: Path | None,
    target_contact_ids: set[str],
) -> tuple[list[dict[str, str]], int]:
    if daily_root is None or not daily_root.exists():
        return [], 0
    rows: list[dict[str, str]] = []
    scanned_files = 0
    for path in sorted(daily_root.rglob("daily_funnel_report_*.csv")):
        source_rows = read_csv_rows(path)
        if not source_rows or "contact_id" not in source_rows[0]:
            continue
        scanned_files += 1
        for row in source_rows:
            contact_id = str(row.get("contact_id") or "").strip()
            opportunity_id = str(row.get("opportunity_id") or "").strip()
            if contact_id in target_contact_ids and opportunity_id:
                rows.append({"contact_id": contact_id, "opportunity_id": opportunity_id})
    return rows, scanned_files


if __name__ == "__main__":
    main()
