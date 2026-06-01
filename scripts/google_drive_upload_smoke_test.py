from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

import sys


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from google_drive_client import GoogleDriveClient  # noqa: E402


def main() -> None:
    load_dotenv(PROJECT_DIR / ".env")

    root_folder_name = os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_NAME", "LionCare").strip() or "LionCare"
    auth_mode = os.getenv("DRIVE_UPLOAD_AUTH_MODE", "service_account").strip().lower()
    token_path = os.getenv("GOOGLE_DRIVE_OAUTH_TOKEN_PATH", "").strip()
    service_account_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()

    if auth_mode == "oauth":
        if not token_path:
            raise SystemExit(
                "Missing GOOGLE_DRIVE_OAUTH_TOKEN_PATH. Run "
                "`python scripts/google_drive_oauth_init.py` first."
            )
        try:
            drive = GoogleDriveClient.from_oauth_token(token_path)
        except FileNotFoundError as exc:
            raise SystemExit(str(exc)) from exc
    else:
        if not service_account_path:
            raise SystemExit("Missing GOOGLE_APPLICATION_CREDENTIALS")
        drive = GoogleDriveClient(service_account_path)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = Path("/private/tmp") / f"lioncare_drive_upload_smoke_test_{stamp}.html"
    csv_path = Path("/private/tmp") / f"lioncare_drive_upload_smoke_test_{stamp}.csv"
    html_path.write_text("<html><body>LionCare Drive OAuth smoke test</body></html>\n")
    csv_path.write_text("status,message\nok,LionCare Drive OAuth smoke test\n")

    root_folder_id = drive.resolve_root_folder_id(root_folder_name)
    html_folder_id = drive.ensure_folder_path(
        ["riport", "daily_html"],
        root_folder_id=root_folder_id,
    )
    csv_folder_id = drive.ensure_folder_path(
        ["riport", "daily_csv"],
        root_folder_id=root_folder_id,
    )
    drive.ensure_folder_path(["riport", "archive"], root_folder_id=root_folder_id)

    html_file_id = drive.upload_file(html_path, folder_id=html_folder_id, filename=html_path.name)
    csv_file_id = drive.upload_file(csv_path, folder_id=csv_folder_id, filename=csv_path.name)

    print("drive_upload_smoke_test=success")
    print(f"root_folder={root_folder_name}")
    print(f"html_file_id={html_file_id}")
    print(f"csv_file_id={csv_file_id}")


if __name__ == "__main__":
    main()
