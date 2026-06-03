from __future__ import annotations

import json
import os
import secrets
import stat
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests
from dotenv import load_dotenv


AUTH_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
SCOPES = "offline_access Files.ReadWrite"


class CallbackHandler(BaseHTTPRequestHandler):
    authorization_code: str | None = None
    returned_state: str | None = None
    error: str | None = None

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        CallbackHandler.authorization_code = params.get("code", [None])[0]
        CallbackHandler.returned_state = params.get("state", [None])[0]
        CallbackHandler.error = params.get("error_description", params.get("error", [None]))[0]
        if CallbackHandler.error:
            body = "Microsoft OneDrive authorization failed. You can close this tab."
            self.send_response(400)
        else:
            body = "Microsoft OneDrive authorization completed. You can close this tab."
            self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    project_dir = Path(__file__).resolve().parents[1]
    load_dotenv(project_dir / ".env")

    client_id = os.getenv("ONEDRIVE_CLIENT_ID", "").strip()
    client_secret = os.getenv("ONEDRIVE_CLIENT_SECRET", "").strip()
    token_path = os.getenv("ONEDRIVE_TOKEN_PATH", str(project_dir / ".secrets" / "onedrive-token.json")).strip()

    if not client_id:
        raise SystemExit("Missing ONEDRIVE_CLIENT_ID in .env")

    token_file = Path(token_path).expanduser()
    token_file.parent.mkdir(parents=True, exist_ok=True)

    server = HTTPServer(("localhost", 0), CallbackHandler)
    redirect_uri = f"http://localhost:{server.server_port}/callback"
    state = secrets.token_urlsafe(24)
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "response_mode": "query",
            "scope": SCOPES,
            "state": state,
            "prompt": "consent",
        }
    )
    authorization_url = f"{AUTH_URL}?{query}"
    print("\nOpen this URL in your browser to authorize OneDrive upload:\n")
    print(authorization_url)
    print()
    webbrowser.open(authorization_url)
    server.handle_request()

    if CallbackHandler.error:
        raise SystemExit(CallbackHandler.error)
    if not CallbackHandler.authorization_code:
        raise SystemExit("Microsoft authorization did not return an authorization code")
    if CallbackHandler.returned_state != state:
        raise SystemExit("Microsoft authorization returned an invalid state")

    payload = {
        "client_id": client_id,
        "grant_type": "authorization_code",
        "code": CallbackHandler.authorization_code,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
    }
    if client_secret:
        payload["client_secret"] = client_secret
    response = requests.post(TOKEN_URL, data=payload, timeout=30)
    response.raise_for_status()
    token_data = response.json()
    token_file.write_text(json.dumps(token_data, indent=2, sort_keys=True))
    token_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    print(f"OneDrive OAuth token saved: {token_file}")
    print("Use the refresh_token value as the ONEDRIVE_REFRESH_TOKEN GitHub Actions secret.")


if __name__ == "__main__":
    main()
