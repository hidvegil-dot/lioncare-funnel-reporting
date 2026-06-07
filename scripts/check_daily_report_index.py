from __future__ import annotations

import argparse
import os
from datetime import date
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build


SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
REQUIRED_COLUMNS = {"date", "report_html_link", "report_csv_link"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether a daily report is indexed in Google Sheets.")
    parser.add_argument("--report-date", required=True, help="Report date in YYYY-MM-DD format.")
    parser.add_argument("--github-output", default=os.getenv("GITHUB_OUTPUT", ""))
    args = parser.parse_args()

    report_date = date.fromisoformat(args.report_date).isoformat()
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    spreadsheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
    if not credentials_path:
        raise SystemExit("Missing GOOGLE_APPLICATION_CREDENTIALS")
    if not spreadsheet_id:
        raise SystemExit("Missing GOOGLE_SHEET_ID")

    service = _sheets_service(credentials_path)
    values = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range="'daily_report_index'!A:S")
        .execute()
        .get("values", [])
    )
    exists, reason, row = _daily_report_exists(values=values, report_date=report_date)
    print(f"report_date={report_date}")
    print(f"exists={str(exists).lower()}")
    print(f"reason={reason}")
    if row:
        print(f"report_html_link={row.get('report_html_link', '')}")
        print(f"report_csv_link={row.get('report_csv_link', '')}")

    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as output:
            output.write(f"exists={str(exists).lower()}\n")
            output.write(f"reason={reason}\n")
            output.write(f"report_date={report_date}\n")

    return 0


def _sheets_service(credentials_path: str) -> Any:
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=[SHEETS_SCOPE],
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _daily_report_exists(*, values: list[list[Any]], report_date: str) -> tuple[bool, str, dict[str, str]]:
    if not values:
        return False, "daily_report_index tab is empty", {}
    header = [str(item) for item in values[0]]
    missing_columns = sorted(REQUIRED_COLUMNS - set(header))
    if missing_columns:
        return False, f"missing columns: {', '.join(missing_columns)}", {}

    date_index = header.index("date")
    rows = [
        dict(zip(header, [str(item) for item in row]))
        for row in values[1:]
        if len(row) > date_index and str(row[date_index]) == report_date
    ]
    if not rows:
        return False, "no daily_report_index row for report date", {}

    latest = rows[-1]
    if not latest.get("report_html_link"):
        return False, "daily_report_index row has empty report_html_link", latest
    if not latest.get("report_csv_link"):
        return False, "daily_report_index row has empty report_csv_link", latest
    return True, "daily_report_index row has Drive links", latest


if __name__ == "__main__":
    raise SystemExit(main())
