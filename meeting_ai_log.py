from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from google_sheets_client import GoogleSheetsClient
from meeting_dates import meeting_date_iso


MEETING_AI_LOG_TAB = "meeting_ai_log"
MEETING_AI_LOG_COLUMNS = [
    "processed_at",
    "meeting_date",
    "fireflies_meeting_id",
    "meeting_title",
    "client_name",
    "closing_probability",
    "confidence_level",
    "interest_level",
    "main_goal",
    "main_objection",
    "main_red_flag",
    "main_hot_trigger",
    "next_action",
    "crm_note_link",
    "followup_draft_link",
    "diagnosis_link",
    "executive_summary_link",
    "output_folder",
    "status",
    "error_message",
]


@dataclass(frozen=True)
class MeetingAILogRow:
    values: list[Any]


class MeetingAILog:
    def __init__(self, sheets: GoogleSheetsClient) -> None:
        self.sheets = sheets
        self.sheets.ensure_tabs({MEETING_AI_LOG_TAB: MEETING_AI_LOG_COLUMNS})

    def processed_ids(self) -> set[str]:
        if os.getenv("MEETING_AI_FORCE_REPROCESS", "").strip().lower() in {"1", "true", "yes", "on"}:
            return set()
        values = (
            self.sheets.service.spreadsheets()
            .values()
            .get(spreadsheetId=self.sheets.spreadsheet_id, range=f"'{MEETING_AI_LOG_TAB}'!A:T")
            .execute()
            .get("values", [])
        )
        if not values:
            return set()
        header = values[0]
        try:
            id_index = header.index("fireflies_meeting_id")
            status_index = header.index("status")
            red_flag_index = header.index("main_red_flag")
        except ValueError:
            return set()
        processed: set[str] = set()
        for row in values[1:]:
            if len(row) <= id_index:
                continue
            status = row[status_index] if len(row) > status_index else ""
            red_flag = row[red_flag_index] if len(row) > red_flag_index else ""
            is_mock = "mock" in str(red_flag).lower()
            if str(status).strip().upper() == "SUCCESS" and not is_mock:
                processed.add(str(row[id_index]))
        return processed

    def append_success(self, *, transcript: dict[str, Any], analysis: dict[str, Any], links: Any) -> None:
        self.sheets.append_row(
            tab_name=MEETING_AI_LOG_TAB,
            values=[
                analysis.get("processed_at", ""),
                meeting_date_iso(transcript.get("date") or analysis.get("meeting_date")),
                transcript.get("id", ""),
                transcript.get("title", ""),
                analysis.get("client_name", ""),
                analysis.get("closing_probability", ""),
                analysis.get("confidence_level", ""),
                analysis.get("interest_level", ""),
                analysis.get("main_goal", ""),
                analysis.get("main_objection", ""),
                analysis.get("main_red_flag", ""),
                analysis.get("main_hot_trigger", ""),
                analysis.get("next_action", ""),
                links.crm_note_link,
                links.followup_draft_link,
                links.diagnosis_link,
                links.executive_summary_link,
                links.output_folder,
                "SUCCESS",
                "",
            ],
        )

    def append_error(self, *, transcript: dict[str, Any], error_message: str, processed_at: str) -> None:
        self.sheets.append_row(
            tab_name=MEETING_AI_LOG_TAB,
            values=[
                processed_at,
                meeting_date_iso(transcript.get("date")),
                transcript.get("id", ""),
                transcript.get("title", ""),
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "ERROR",
                error_message[:500],
            ],
        )
