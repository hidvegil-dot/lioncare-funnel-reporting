from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ghl_client import GHLClient, GHLConfig
from google_sheets_client import GoogleSheetsClient
from parser import SHEET_TABS, build_historical_rows
from report_builder import (
    build_daily_decision_report,
    build_report_rows,
    overlay_funnel_counts_from_appointments,
    summarize_period,
)


logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill GHL-based daily Google Sheet tabs without generating daily report files."
    )
    parser.add_argument("--start-date", required=True, help="Inclusive start date in YYYY-MM-DD format.")
    parser.add_argument("--end-date", required=True, help="Inclusive end date in YYYY-MM-DD format.")
    return parser.parse_args()


def parse_iso_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def each_day(start_date: date, end_date: date) -> list[date]:
    days: list[date] = []
    cursor = start_date
    while cursor <= end_date:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("backfill_daily_ghl_sheet.log", encoding="utf-8"),
        ],
    )


def filter_contacts_for_day(client: GHLClient, contacts: list[dict[str, Any]], report_date: date) -> list[dict[str, Any]]:
    return [
        contact
        for contact in contacts
        if client._contact_matches_window(contact, report_date, report_date)
    ]


def filter_appointments_for_day(
    client: GHLClient,
    appointments: list[dict[str, Any]],
    report_date: date,
) -> list[dict[str, Any]]:
    daily: list[dict[str, Any]] = []
    for appointment in appointments:
        appointment_date = client._extract_appointment_date(appointment)
        if appointment_date == report_date:
            daily.append(appointment)
    return daily


def replace_date_range_rows(
    sheets: GoogleSheetsClient,
    *,
    tab_name: str,
    start_date: date,
    end_date: date,
    rows: list[list[Any]],
) -> None:
    range_name = f"'{tab_name}'!A:Z"
    response = (
        sheets.service.spreadsheets()
        .values()
        .get(spreadsheetId=sheets.spreadsheet_id, range=range_name)
        .execute()
    )
    values = response.get("values", [])
    header = values[:1]
    existing_rows = values[1:]
    kept_rows = []
    for row in existing_rows:
        if not row:
            kept_rows.append(row)
            continue
        try:
            row_date = parse_iso_date(str(row[0]))
        except ValueError:
            kept_rows.append(row)
            continue
        if not (start_date <= row_date <= end_date):
            kept_rows.append(row)

    next_values = header + kept_rows + rows
    logger.info(
        "Replacing Google Sheets date range tab=%s start=%s end=%s row_count=%s",
        tab_name,
        start_date,
        end_date,
        len(rows),
    )
    sheets.service.spreadsheets().values().clear(
        spreadsheetId=sheets.spreadsheet_id,
        range=range_name,
        body={},
    ).execute()
    if next_values:
        sheets.service.spreadsheets().values().update(
            spreadsheetId=sheets.spreadsheet_id,
            range=f"'{tab_name}'!A1",
            valueInputOption="USER_ENTERED",
            body={"values": next_values},
        ).execute()


def main() -> None:
    load_dotenv()
    configure_logging()
    args = parse_args()
    start_date = parse_iso_date(args.start_date)
    end_date = parse_iso_date(args.end_date)
    if end_date < start_date:
        raise ValueError("end-date must be later than or equal to start-date")

    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    spreadsheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
    if not credentials_path:
        raise ValueError("Missing GOOGLE_APPLICATION_CREDENTIALS environment variable")
    if not spreadsheet_id:
        raise ValueError("Missing GOOGLE_SHEET_ID environment variable")

    started_at = time.perf_counter()
    logger.info("Starting daily GHL Sheet backfill start=%s end=%s", start_date, end_date)
    ghl = GHLClient(GHLConfig.from_env())

    contacts_started_at = time.perf_counter()
    current_crm_contacts = ghl.fetch_all_contacts()
    logger.info(
        "Fetched %s GHL contacts in %.2fs",
        len(current_crm_contacts),
        time.perf_counter() - contacts_started_at,
    )

    appointments_started_at = time.perf_counter()
    appointments = ghl.fetch_appointments_for_contacts(
        contacts=current_crm_contacts,
        start_date=start_date,
        end_date=end_date,
    )
    logger.info(
        "Fetched %s GHL appointments in %.2fs",
        len(appointments),
        time.perf_counter() - appointments_started_at,
    )

    sheets = GoogleSheetsClient(credentials_path, spreadsheet_id)
    sheets.ensure_tabs(SHEET_TABS)
    backfill_rows: dict[str, list[list[Any]]] = {tab_name: [] for tab_name in SHEET_TABS}

    for report_date in each_day(start_date, end_date):
        day_started_at = time.perf_counter()
        contacts = filter_contacts_for_day(ghl, current_crm_contacts, report_date)
        daily_appointments = filter_appointments_for_day(ghl, appointments, report_date)
        closed_meeting_counts: dict[str, int] = {}
        rows = build_report_rows(
            contacts=contacts,
            closed_meeting_counts=closed_meeting_counts,
            start_date=report_date,
            end_date=report_date,
        )
        rows = overlay_funnel_counts_from_appointments(rows=rows, appointments=daily_appointments)
        summary = summarize_period(rows=rows, closed_meeting_counts=closed_meeting_counts)
        decision_report = build_daily_decision_report(
            report_date=report_date,
            summary=summary,
            ga4_data=None,
            meta_data=None,
            contacts=contacts,
            current_crm_contacts=current_crm_contacts,
        )
        historical_rows = build_historical_rows(
            report_date=report_date,
            summary=summary,
            decision_report=decision_report,
            ga4_data=None,
            meta_data=None,
            created_at=datetime.now(),
        )
        for tab_name, tab_rows in historical_rows.items():
            backfill_rows[tab_name].extend(tab_rows)
        logger.info(
            "Prepared date=%s contacts=%s appointments=%s sheet_rows=%s in %.2fs",
            report_date,
            len(contacts),
            len(daily_appointments),
            sum(len(tab_rows) for tab_rows in historical_rows.values()),
            time.perf_counter() - day_started_at,
        )

    for tab_name, rows in backfill_rows.items():
        replace_date_range_rows(
            sheets,
            tab_name=tab_name,
            start_date=start_date,
            end_date=end_date,
            rows=rows,
        )

    logger.info("Completed daily GHL Sheet backfill in %.2fs", time.perf_counter() - started_at)


if __name__ == "__main__":
    main()
