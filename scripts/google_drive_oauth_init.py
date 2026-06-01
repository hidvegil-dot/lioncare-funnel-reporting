from __future__ import annotations

import os
import stat
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow


DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


def main() -> None:
    project_dir = Path(__file__).resolve().parents[1]
    load_dotenv(project_dir / ".env")

    client_secret_path = os.getenv("GOOGLE_DRIVE_OAUTH_CLIENT_SECRET_PATH", "").strip()
    token_path = os.getenv("GOOGLE_DRIVE_OAUTH_TOKEN_PATH", "").strip()

    if not client_secret_path:
        raise SystemExit("Missing GOOGLE_DRIVE_OAUTH_CLIENT_SECRET_PATH in .env")
    if not token_path:
        raise SystemExit("Missing GOOGLE_DRIVE_OAUTH_TOKEN_PATH in .env")

    client_secret_file = Path(client_secret_path).expanduser()
    if not client_secret_file.exists():
        raise SystemExit(f"OAuth client secret file not found: {client_secret_file}")

    token_file = Path(token_path).expanduser()
    token_file.parent.mkdir(parents=True, exist_ok=True)

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secret_file),
        scopes=[DRIVE_SCOPE],
    )
    credentials = flow.run_local_server(
        host="localhost",
        port=0,
        open_browser=False,
        authorization_prompt_message=(
            "\nOpen this URL in your browser to authorize Google Drive upload:\n{url}\n"
        ),
        success_message="Google Drive OAuth authorization completed. You can close this tab.",
        access_type="offline",
        prompt="consent",
    )

    token_file.write_text(credentials.to_json())
    token_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    print(f"Google Drive OAuth token saved: {token_file}")


if __name__ == "__main__":
    main()
