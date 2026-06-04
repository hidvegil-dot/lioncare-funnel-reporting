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


WEEKLY_AI_ANALYSIS_COLUMNS = [
    "week_start",
    "week_end",
    "new_leads",
    "bookings",
    "showed",
    "no_show",
    "cancelled",
    "won",
    "lost",
    "lead_to_booking_rate",
    "booking_to_show_rate",
    "show_to_close_rate",
    "main_bottleneck",
    "main_problem",
    "main_opportunity",
    "recommended_action_1",
    "recommended_action_2",
    "recommended_action_3",
    "advisor_laszlo_summary",
    "advisor_amelita_summary",
    "crm_data_quality_note",
    "created_at",
]


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


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
    strict_storage = _env_flag("REPORT_STORAGE_STRICT")
    if config is None:
        message = (
            "Skipping historical report storage because REPORT_HISTORY_ENABLED is disabled "
            "or Google env vars are missing"
        )
        if strict_storage:
            raise RuntimeError(message)
        logger.info(message)
        return

    storage_failures: list[str] = []
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
        daily_folder_id = drive.ensure_folder_path(
            [
                "riport",
                "daily",
                f"{report_date.year:04d}",
                f"{report_date.month:02d}",
                report_date.isoformat(),
            ],
            root_folder_id=root_folder_id,
        )
        drive.ensure_folder_path(["riport", "archive"], root_folder_id=root_folder_id)
        drive.upload_file(dated_html_path, folder_id=daily_folder_id, filename="daily_funnel_report.html")
        drive.upload_file(dated_csv_path, folder_id=daily_folder_id, filename="daily_funnel_report.csv")
        logger.info("Completed Google Drive daily report upload date=%s", report_date)
    except _DriveUploadSkipped:
        pass
    except Exception as exc:
        logger.exception("Google Drive upload failed; local report files remain available")
        if strict_storage:
            storage_failures.append(f"Google Drive upload failed: {exc}")

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
    except Exception as exc:
        logger.exception("Google Sheets append failed; local report files remain available")
        if strict_storage:
            storage_failures.append(f"Google Sheets upsert failed: {exc}")

    if storage_failures:
        raise RuntimeError("; ".join(storage_failures))


def _copy_dated_report(*, source_path: Path, output_dir: Path, filename: str) -> Path:
    target_dir = output_dir / "archive"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename
    shutil.copy2(source_path, target_path)
    logger.info("Wrote dated local archive file %s", target_path)
    return target_path


def persist_weekly_ai_analysis(
    *,
    week_start: date,
    week_end: date,
    html_path: Path,
    summary_path: Path,
    csv_path: Path,
    output_dir: Path,
    report: dict[str, Any],
) -> None:
    config = ReportStorageConfig.from_env_optional()
    strict_storage = _env_flag("REPORT_STORAGE_STRICT")
    if config is None:
        message = "Skipping weekly report storage because Google env vars are missing or history is disabled"
        if strict_storage:
            raise RuntimeError(message)
        logger.info(message)
        return

    failures: list[str] = []
    dated_html = _copy_dated_report(
        source_path=html_path,
        output_dir=output_dir,
        filename=f"weekly_ghl_funnel_report_{week_start.isoformat()}_{week_end.isoformat()}.html",
    )
    dated_summary = _copy_dated_report(
        source_path=summary_path,
        output_dir=output_dir,
        filename=f"weekly_ghl_ceo_summary_{week_start.isoformat()}_{week_end.isoformat()}.md",
    )
    dated_csv = _copy_dated_report(
        source_path=csv_path,
        output_dir=output_dir,
        filename=f"weekly_ghl_funnel_report_{week_start.isoformat()}_{week_end.isoformat()}.csv",
    )

    if _env_flag("REPORT_DRIVE_UPLOAD_ENABLED", "true"):
        try:
            from google_drive_client import GoogleDriveClient

            if config.drive_upload_auth_mode == "oauth":
                if not config.drive_oauth_token_path:
                    raise ValueError("GOOGLE_DRIVE_OAUTH_TOKEN_PATH is required when DRIVE_UPLOAD_AUTH_MODE=oauth")
                drive = GoogleDriveClient.from_oauth_token(config.drive_oauth_token_path)
            else:
                drive = GoogleDriveClient(config.credentials_path)
            root_folder_id = drive.resolve_root_folder_id(config.drive_root_folder_name)
            weekly_folder_id = drive.ensure_folder_path(["riport", "weekly"], root_folder_id=root_folder_id)
            drive.upload_file(dated_html, folder_id=weekly_folder_id, filename=dated_html.name)
            drive.upload_file(dated_summary, folder_id=weekly_folder_id, filename=dated_summary.name)
            drive.upload_file(dated_csv, folder_id=weekly_folder_id, filename=dated_csv.name)
            logger.info("Completed Google Drive weekly report upload week_start=%s", week_start)
        except Exception as exc:
            logger.exception("Google Drive weekly upload failed")
            if strict_storage:
                failures.append(f"Google Drive weekly upload failed: {exc}")

    try:
        from google_sheets_client import GoogleSheetsClient
        from weekly_ai_summary import advisor_summary

        metrics = report["metrics"]
        diagnosis = report["diagnosis"]
        advisor_rows = {row["advisor_key"]: row for row in metrics.get("advisor_rows", [])}
        actions = list(diagnosis.get("recommended_actions", []))[:3]
        while len(actions) < 3:
            actions.append("")
        row = [
            week_start.isoformat(),
            week_end.isoformat(),
            metrics.get("new_leads", 0),
            metrics.get("bookings", 0),
            metrics.get("showed", 0),
            metrics.get("no_show", 0),
            metrics.get("cancelled", 0),
            metrics.get("won", 0),
            metrics.get("lost", 0),
            metrics.get("lead_to_booking_rate", 0),
            metrics.get("booking_to_show_rate", 0),
            metrics.get("show_to_close_rate", 0),
            diagnosis.get("main_bottleneck", ""),
            diagnosis.get("main_problem", ""),
            diagnosis.get("main_opportunity", ""),
            actions[0],
            actions[1],
            actions[2],
            advisor_summary(advisor_rows.get("hidvegi_laszlo", {})),
            advisor_summary(advisor_rows.get("gulyas_amelita", {})),
            diagnosis.get("crm_data_quality_note", ""),
            datetime.now().isoformat(timespec="seconds"),
        ]
        sheets = GoogleSheetsClient(config.credentials_path, config.spreadsheet_id)
        sheets.ensure_tabs({"weekly_ai_analysis": WEEKLY_AI_ANALYSIS_COLUMNS})
        sheets.replace_date_rows(
            tab_name="weekly_ai_analysis",
            date_value=week_start.isoformat(),
            rows=[row],
        )
        logger.info("Completed Google Sheets weekly_ai_analysis upsert week_start=%s", week_start)
    except Exception as exc:
        logger.exception("Google Sheets weekly_ai_analysis upsert failed")
        if strict_storage:
            failures.append(f"Google Sheets weekly_ai_analysis upsert failed: {exc}")

    if failures:
        raise RuntimeError("; ".join(failures))


class _DriveUploadSkipped(Exception):
    pass
