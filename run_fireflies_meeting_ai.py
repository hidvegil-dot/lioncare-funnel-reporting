from __future__ import annotations

import argparse
import logging
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from meeting_ai_processor import MeetingAIConfig, MeetingAIProcessor


logger = logging.getLogger(__name__)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run LionCare Meeting AI daily Fireflies batch.")
    parser.add_argument("--lookback-hours", type=int, default=int(os.getenv("MEETING_AI_LOOKBACK_HOURS", "24")))
    parser.add_argument("--limit", type=int, default=int(os.getenv("MEETING_AI_TRANSCRIPT_LIMIT", "25")))
    args = parser.parse_args()

    configure_logging()
    credentials_path = resolve_google_credentials_path()
    config = MeetingAIConfig(
        google_credentials_path=credentials_path,
        google_sheet_id=_required_env("GOOGLE_SHEET_ID"),
        google_drive_root_folder_name=os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_NAME", "LionCare").strip() or "LionCare",
        lookback_hours=args.lookback_hours,
        transcript_limit=args.limit,
    )
    counters = MeetingAIProcessor(config).run()
    logger.info("Meeting AI completed counters=%s", counters)


def resolve_google_credentials_path() -> str:
    path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if path:
        return path
    raw_json = (
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()
        or os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    )
    if not raw_json:
        raise ValueError(
            "Missing GOOGLE_APPLICATION_CREDENTIALS or GOOGLE_APPLICATION_CREDENTIALS_JSON "
            "or GOOGLE_SERVICE_ACCOUNT_JSON"
        )
    temp_path = Path(tempfile.gettempdir()) / "meeting-ai-google-service-account.json"
    temp_path.write_text(raw_json, encoding="utf-8")
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(temp_path)
    return str(temp_path)


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing {name} environment variable")
    return value


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("meeting_ai_run.log", encoding="utf-8"),
        ],
    )


if __name__ == "__main__":
    main()
