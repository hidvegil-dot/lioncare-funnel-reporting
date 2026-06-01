from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from parser import SHEET_TABS, build_historical_rows


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReportStorageConfig:
    credentials_path: str
    spreadsheet_id: str
    drive_root_folder_name: str = "LionCare"
    drive_upload_auth_mode: str = "service_account"
    drive_oauth_token_path: str | None = None
    enabled: bool = True

    @classmethod
    def from_env_optional(cls) -> "ReportStorageConfig | None":
        credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        spreadsheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
        enabled = os.getenv("REPORT_HISTORY_ENABLED", "true").strip().lower() not in {
            "0",
            "false",
            "no",
        }
        if not enabled:
            return None
        if not credentials_path or not spreadsheet_id:
            return None
        return cls(
            credentials_path=credentials_path,
            spreadsheet_id=spreadsheet_id,
            drive_root_folder_name=(
                os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_NAME", "LionCare").strip() or "LionCare"
            ),
            drive_upload_auth_mode=(
                os.getenv("DRIVE_UPLOAD_AUTH_MODE", "service_account").strip().lower()
                or "service_account"
            ),
            drive_oauth_token_path=os.getenv("GOOGLE_DRIVE_OAUTH_TOKEN_PATH", "").strip() or None,
            enabled=enabled,
        )


def persist_daily_report_history(
    *,
    report_date: date,
    html_path: Path,
    csv_path: Path,
    output_dir: Path,
    summary: dict[str, Any],
    decision_report: dict[str, Any] | None,
    ga4_data: dict[str, Any] | None,
    meta_data: dict[str, Any] | None,
) -> None:
    config = ReportStorageConfig.from_env_optional()
    if config is None:
        logger.info(
            "Skipping historical report storage because REPORT_HISTORY_ENABLED is disabled or Google env vars are missing"
        )
        return

    dated_html_path = _copy_dated_report(
        source_path=html_path,
        output_dir=output_dir,
        filename=f"daily_funnel_report_{report_date.isoformat()}.html",
    )
    dated_csv_path = _copy_dated_report(
        source_path=csv_path,
        output_dir=output_dir,
        filename=f"daily_funnel_report_{report_date.isoformat()}.csv",
    )

    drive_upload_enabled = os.getenv("REPORT_DRIVE_UPLOAD_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }
    if not drive_upload_enabled:
        logger.info("Skipping Google Drive upload because REPORT_DRIVE_UPLOAD_ENABLED is disabled")
    try:
        if not drive_upload_enabled:
            raise _DriveUploadSkipped()
        from google_drive_client import GoogleDriveClient

        logger.info(
            "Starting Google Drive daily report upload date=%s root_folder=%s auth_mode=%s",
            report_date,
            config.drive_root_folder_name,
            config.drive_upload_auth_mode,
        )
        if config.drive_upload_auth_mode == "oauth":
            if not config.drive_oauth_token_path:
                raise ValueError(
                    "GOOGLE_DRIVE_OAUTH_TOKEN_PATH is required when DRIVE_UPLOAD_AUTH_MODE=oauth. "
                    "Run `python scripts/google_drive_oauth_init.py` first."
                )
            drive = GoogleDriveClient.from_oauth_token(config.drive_oauth_token_path)
        elif config.drive_upload_auth_mode in {"service_account", "service-account"}:
            drive = GoogleDriveClient(config.credentials_path)
        else:
            raise ValueError(
                "Unsupported DRIVE_UPLOAD_AUTH_MODE: "
                f"{config.drive_upload_auth_mode}. Use `oauth` or `service_account`."
            )
        root_folder_id = drive.resolve_root_folder_id(config.drive_root_folder_name)
        html_folder_id = drive.ensure_folder_path(
            ["riport", "daily_html"],
            root_folder_id=root_folder_id,
        )
        csv_folder_id = drive.ensure_folder_path(
            ["riport", "daily_csv"],
            root_folder_id=root_folder_id,
        )
        drive.ensure_folder_path(["riport", "archive"], root_folder_id=root_folder_id)
        drive.upload_file(dated_html_path, folder_id=html_folder_id, filename=dated_html_path.name)
        drive.upload_file(dated_csv_path, folder_id=csv_folder_id, filename=dated_csv_path.name)
        logger.info("Completed Google Drive daily report upload date=%s", report_date)
    except _DriveUploadSkipped:
        pass
    except Exception:
        logger.exception("Google Drive upload failed; local report files remain available")

    try:
        from google_sheets_client import GoogleSheetsClient

        logger.info("Starting Google Sheets historical upsert date=%s", report_date)
        sheets = GoogleSheetsClient(config.credentials_path, config.spreadsheet_id)
        sheets.ensure_tabs(SHEET_TABS)
        historical_rows = build_historical_rows(
            report_date=report_date,
            summary=summary,
            decision_report=decision_report,
            ga4_data=ga4_data,
            meta_data=meta_data,
            created_at=datetime.now(),
        )
        for tab_name, rows in historical_rows.items():
            sheets.replace_date_rows(
                tab_name=tab_name,
                date_value=report_date.isoformat(),
                rows=rows,
            )
        logger.info("Completed Google Sheets historical upsert date=%s", report_date)
    except Exception:
        logger.exception("Google Sheets append failed; local report files remain available")


def _copy_dated_report(*, source_path: Path, output_dir: Path, filename: str) -> Path:
    target_dir = output_dir / "archive"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename
    shutil.copy2(source_path, target_path)
    logger.info("Wrote dated local archive file %s", target_path)
    return target_path


class _DriveUploadSkipped(Exception):
    pass
