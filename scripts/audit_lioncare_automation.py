from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from google_drive_client import GoogleDriveClient  # noqa: E402


REQUIRED_MEETING_FILES = {
    "transcript.md",
    "crm-note.md",
    "followup.md",
    "diagnosis.md",
    "summary.md",
}


def main() -> int:
    load_dotenv(PROJECT_DIR / ".env")
    failures: list[str] = []
    warnings: list[str] = []

    drive = _drive_client()
    root_name = os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_NAME", "LionCare").strip() or "LionCare"
    root_id = drive.resolve_root_folder_id(root_name)
    _audit_drive(drive, root_id, failures, warnings)
    _audit_sheets(failures, warnings)

    print("LionCare automation audit")
    for item in warnings:
        print(f"WARNING: {item}")
    for item in failures:
        print(f"FAIL: {item}")
    if not warnings and not failures:
        print("OK: no issues found")
    return 1 if failures else 0


def _audit_drive(
    drive: GoogleDriveClient,
    root_id: str,
    failures: list[str],
    warnings: list[str],
) -> None:
    meeting_ai_folders = _find_folders(drive, parent_id=root_id, name="meeting_ai")
    canonical = None
    for folder in meeting_ai_folders:
        children = _children(drive, folder["id"])
        if any(child["name"] == "Meet" for child in children):
            canonical = folder
    if canonical is None:
        failures.append("Drive missing canonical LionCare/meeting_ai folder with Meet child")
        return
    if len(meeting_ai_folders) > 1:
        warnings.append(f"Drive has {len(meeting_ai_folders)} meeting_ai-like root folders; legacy copies should stay archived")

    meet_folder = next(
        child for child in _children(drive, canonical["id"]) if child["name"] == "Meet"
    )
    meeting_folders = [
        child
        for child in _children(drive, meet_folder["id"])
        if child["mimeType"] == "application/vnd.google-apps.folder"
    ]
    for meeting_folder in meeting_folders:
        child_names = {child["name"] for child in _children(drive, meeting_folder["id"])}
        missing = sorted(REQUIRED_MEETING_FILES - child_names)
        if missing:
            failures.append(f"Meeting folder {meeting_folder['name']} missing files: {', '.join(missing)}")

    if _resolve_folder_path(drive, root_id, ["riport", "daily"]) is None:
        failures.append("Drive missing LionCare/riport/daily")


def _audit_sheets(failures: list[str], warnings: list[str]) -> None:
    service = _sheets_service()
    spreadsheet_id = _required_env("GOOGLE_SHEET_ID")
    tabs = {
        sheet["properties"]["title"]
        for sheet in service.spreadsheets()
        .get(spreadsheetId=spreadsheet_id, fields="sheets(properties(title))")
        .execute()
        .get("sheets", [])
    }
    required_tabs = {
        "daily_ghl_summary",
        "daily_ghl_diagnosis",
        "daily_ghl_status",
        "daily_ghl_owner",
        "daily_ghl_landing",
        "weekly_ai_analysis",
        "meeting_ai_log",
    }
    for tab in sorted(required_tabs - tabs):
        failures.append(f"Google Sheet missing required tab: {tab}")

    values = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range="'meeting_ai_log'!A:Z")
        .execute()
        .get("values", [])
    )
    if values:
        header = values[0]
        required_columns = {"fireflies_meeting_id", "status", "output_folder", "transcript_link"}
        for column in sorted(required_columns - set(header)):
            failures.append(f"meeting_ai_log missing column: {column}")
        if "fireflies_meeting_id" in header:
            id_index = header.index("fireflies_meeting_id")
            ids = [row[id_index] for row in values[1:] if len(row) > id_index and row[id_index]]
            duplicates = sorted({meeting_id for meeting_id in ids if ids.count(meeting_id) > 1})
            for meeting_id in duplicates:
                failures.append(f"meeting_ai_log duplicate fireflies_meeting_id: {meeting_id}")
    else:
        warnings.append("meeting_ai_log is empty")


def _drive_client() -> GoogleDriveClient:
    token_path = os.getenv("GOOGLE_DRIVE_OAUTH_TOKEN_PATH", "").strip()
    if not token_path:
        raise SystemExit("Missing GOOGLE_DRIVE_OAUTH_TOKEN_PATH")
    return GoogleDriveClient.from_oauth_token(token_path)


def _sheets_service():
    raw_json = (
        os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()
    )
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if raw_json:
        temp_path = Path(tempfile.gettempdir()) / "lioncare-audit-google-service-account.json"
        temp_path.write_text(raw_json, encoding="utf-8")
        credentials_path = str(temp_path)
    if not credentials_path:
        raise SystemExit("Missing Google Sheets service account credentials")
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _find_folders(drive: GoogleDriveClient, *, parent_id: str, name: str) -> list[dict[str, str]]:
    escaped_name = name.replace("\\", "\\\\").replace("'", "\\'")
    escaped_parent = parent_id.replace("\\", "\\\\").replace("'", "\\'")
    response = (
        drive.service.files()
        .list(
            q=(
                f"name = '{escaped_name}' and mimeType = 'application/vnd.google-apps.folder' "
                f"and '{escaped_parent}' in parents and trashed = false"
            ),
            spaces="drive",
            fields="files(id,name,mimeType)",
            pageSize=100,
            corpora="user",
        )
        .execute()
    )
    return response.get("files", [])


def _children(drive: GoogleDriveClient, parent_id: str) -> list[dict[str, str]]:
    escaped_parent = parent_id.replace("\\", "\\\\").replace("'", "\\'")
    response = (
        drive.service.files()
        .list(
            q=f"'{escaped_parent}' in parents and trashed = false",
            spaces="drive",
            fields="files(id,name,mimeType)",
            pageSize=100,
            corpora="user",
        )
        .execute()
    )
    return response.get("files", [])


def _resolve_folder_path(
    drive: GoogleDriveClient,
    root_id: str,
    path_parts: list[str],
) -> str | None:
    parent_id = root_id
    for part in path_parts:
        folders = _find_folders(drive, parent_id=parent_id, name=part)
        if not folders:
            return None
        parent_id = folders[0]["id"]
    return parent_id


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing {name}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
