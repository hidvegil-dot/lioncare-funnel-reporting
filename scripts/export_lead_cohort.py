from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta
from pathlib import Path

from daily_split_exports import LEAD_COHORT_COLUMNS, build_lead_cohort_rows, current_budapest_timestamp, write_csv
from ghl_client import GHLClient, GHLConfig
from report_builder import build_daily_lead_csv_rows


logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a lead cohort CSV for a date range.")
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def iter_dates(start_date: date, end_date: date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def main() -> None:
    args = parse_args()
    if args.end_date < args.start_date:
        raise ValueError("end_date must be on or after start_date")

    client = GHLClient(GHLConfig.from_env())
    cohort_contacts = client.fetch_contacts_for_window(args.start_date, args.end_date)
    all_contacts = client.fetch_all_contacts()
    opportunities = client.fetch_opportunities()
    appointments = client.fetch_appointments_for_contacts(
        contacts=all_contacts,
        end_date=args.end_date,
    )

    daily_rows = []
    for report_date in iter_dates(args.start_date, args.end_date):
        daily_rows.extend(
            build_daily_lead_csv_rows(
                report_date=report_date,
                contacts=cohort_contacts,
                appointments=appointments,
                opportunities=opportunities,
            )
        )

    exported_at = current_budapest_timestamp()
    rows, duplicates = build_lead_cohort_rows(
        daily_lead_rows=daily_rows,
        exported_at=exported_at,
        data_as_of=exported_at,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_csv(args.output, LEAD_COHORT_COLUMNS, rows)
    logger.info(
        "Exported %s cohort rows for %s to %s (deduplicated=%s)",
        len(rows),
        args.start_date.isoformat(),
        args.output,
        len(duplicates),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    main()
