from __future__ import annotations

import logging
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build


logger = logging.getLogger(__name__)

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


class GoogleSheetsClient:
    def __init__(self, credentials_path: str, spreadsheet_id: str) -> None:
        credentials = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=[SHEETS_SCOPE],
        )
        self.spreadsheet_id = spreadsheet_id
        self.service = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    def ensure_tabs(self, tabs: dict[str, list[str]]) -> None:
        spreadsheet = (
            self.service.spreadsheets()
            .get(spreadsheetId=self.spreadsheet_id, fields="sheets(properties(title))")
            .execute()
        )
        existing_titles = {
            sheet["properties"]["title"]
            for sheet in spreadsheet.get("sheets", [])
            if "properties" in sheet
        }

        requests: list[dict[str, Any]] = []
        for title in tabs:
            if title not in existing_titles:
                logger.info("Creating Google Sheets tab title=%s", title)
                requests.append({"addSheet": {"properties": {"title": title}}})

        if requests:
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": requests},
            ).execute()

        for title, headers in tabs.items():
            self._ensure_header(title=title, headers=headers)

    def append_row(self, *, tab_name: str, values: list[Any]) -> None:
        logger.info("Appending Google Sheets row tab=%s", tab_name)
        self.service.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{tab_name}'!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [values]},
        ).execute()

    def replace_date_rows(self, *, tab_name: str, date_value: str, rows: list[list[Any]]) -> None:
        """Replace all rows for one report date so re-runs do not duplicate history."""
        logger.info(
            "Replacing Google Sheets rows tab=%s date=%s row_count=%s",
            tab_name,
            date_value,
            len(rows),
        )
        range_name = f"'{tab_name}'!A:{_column_letter(max(len(row) for row in rows) if rows else 26)}"
        response = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=range_name)
            .execute()
        )
        values = response.get("values", [])
        header = values[:1]
        existing_rows = values[1:]
        kept_rows = [
            row
            for row in existing_rows
            if not row or str(row[0]) != date_value
        ]
        next_values = header + kept_rows + rows

        self.service.spreadsheets().values().clear(
            spreadsheetId=self.spreadsheet_id,
            range=range_name,
            body={},
        ).execute()
        if next_values:
            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{tab_name}'!A1",
                valueInputOption="USER_ENTERED",
                body={"values": next_values},
            ).execute()

    def upsert_row_by_key(
        self,
        *,
        tab_name: str,
        key_column: str,
        key_value: str,
        row_values: list[Any],
    ) -> None:
        logger.info("Upserting Google Sheets row tab=%s key_column=%s key=%s", tab_name, key_column, key_value)
        range_name = f"'{tab_name}'!A:{_column_letter(len(row_values))}"
        response = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=range_name)
            .execute()
        )
        values = response.get("values", [])
        if not values:
            next_values = [row_values]
        else:
            header = values[0]
            try:
                key_index = header.index(key_column)
            except ValueError as exc:
                raise ValueError(f"Missing key column {key_column!r} in tab {tab_name!r}") from exc

            next_values = [header]
            replaced = False
            for existing_row in values[1:]:
                existing_key = existing_row[key_index] if len(existing_row) > key_index else ""
                if str(existing_key) == str(key_value):
                    if not replaced:
                        next_values.append(row_values)
                        replaced = True
                    continue
                next_values.append(existing_row)
            if not replaced:
                next_values.append(row_values)

        self.service.spreadsheets().values().clear(
            spreadsheetId=self.spreadsheet_id,
            range=range_name,
            body={},
        ).execute()
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{tab_name}'!A1",
            valueInputOption="USER_ENTERED",
            body={"values": next_values},
        ).execute()

    def _ensure_header(self, *, title: str, headers: list[str]) -> None:
        header_range = f"'{title}'!A1:{_column_letter(len(headers))}1"
        response = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=header_range)
            .execute()
        )
        current = response.get("values", [[]])
        if current and current[0] == headers:
            return

        logger.info("Writing Google Sheets header tab=%s", title)
        self.service.spreadsheets().values().clear(
            spreadsheetId=self.spreadsheet_id,
            range=header_range,
            body={},
        ).execute()
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{title}'!A1",
            valueInputOption="RAW",
            body={"values": [headers]},
        ).execute()


def _column_letter(column_count: int) -> str:
    if column_count < 1:
        raise ValueError("column_count must be positive")
    letters = ""
    value = column_count
    while value:
        value, remainder = divmod(value - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters
