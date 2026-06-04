from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from client_communication_ai import ClientCommunicationAI, OpenAIConfig, build_transcript_text
from fireflies_client import FirefliesClient, FirefliesConfig
from google_drive_client import GoogleDriveClient
from google_sheets_client import GoogleSheetsClient
from meeting_ai_log import MeetingAILog
from meeting_output_writer import MeetingOutputWriter


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MeetingAIConfig:
    google_credentials_path: str
    google_sheet_id: str
    google_drive_root_folder_name: str
    lookback_hours: int = 24
    transcript_limit: int = 25


class MeetingAIProcessor:
    def __init__(self, config: MeetingAIConfig) -> None:
        self.config = config
        self.fireflies = FirefliesClient(FirefliesConfig.from_env())
        self.ai = ClientCommunicationAI(OpenAIConfig.from_env())
        self.sheets = GoogleSheetsClient(config.google_credentials_path, config.google_sheet_id)
        self.log = MeetingAILog(self.sheets)
        self.writer = MeetingOutputWriter(self._build_drive_client(), root_folder_name=config.google_drive_root_folder_name)

    def run(self) -> dict[str, int]:
        processed_ids = self.log.processed_ids()
        transcripts = self._fetch_recent_transcripts()
        counters = {"found": len(transcripts), "skipped": 0, "processed": 0, "errors": 0}
        logger.info("Fetched %s recent Fireflies transcripts", len(transcripts))
        for transcript_meta in transcripts:
            meeting_id = str(transcript_meta.get("id") or "")
            if not meeting_id:
                counters["skipped"] += 1
                continue
            if meeting_id in processed_ids:
                logger.info("Skipping already processed Fireflies meeting id=%s", meeting_id)
                counters["skipped"] += 1
                continue
            try:
                transcript = self.fireflies.get_transcript(meeting_id, include_sentences=True)
                if not build_transcript_text(transcript).strip():
                    logger.info("Skipping Fireflies meeting without transcript text id=%s", meeting_id)
                    counters["skipped"] += 1
                    continue
                analysis = self.ai.analyze_meeting(transcript)
                links = self.writer.write_outputs(transcript=transcript, analysis=analysis)
                self.log.append_success(transcript=transcript, analysis=analysis, links=links)
                counters["processed"] += 1
                logger.info("Processed Fireflies meeting id=%s title=%s", meeting_id, transcript.get("title", ""))
            except Exception as exc:
                counters["errors"] += 1
                logger.exception("Meeting AI processing failed for Fireflies meeting id=%s", meeting_id)
                self.log.append_error(
                    transcript=transcript_meta,
                    error_message=str(exc),
                    processed_at=datetime.now().isoformat(timespec="seconds"),
                )
        return counters

    def _fetch_recent_transcripts(self) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        from_dt = now - timedelta(hours=self.config.lookback_hours)
        return self.fireflies.list_transcripts(
            limit=self.config.transcript_limit,
            from_date=from_dt.isoformat().replace("+00:00", "Z"),
            to_date=now.isoformat().replace("+00:00", "Z"),
            mine=True,
        )

    def _build_drive_client(self) -> GoogleDriveClient:
        auth_mode = os.getenv("DRIVE_UPLOAD_AUTH_MODE", "service_account").strip().lower()
        if auth_mode == "oauth":
            token_path = os.getenv("GOOGLE_DRIVE_OAUTH_TOKEN_PATH", "").strip()
            if not token_path:
                raise ValueError("GOOGLE_DRIVE_OAUTH_TOKEN_PATH is required when DRIVE_UPLOAD_AUTH_MODE=oauth")
            return GoogleDriveClient.from_oauth_token(token_path)
        return GoogleDriveClient(self.config.google_credentials_path)
