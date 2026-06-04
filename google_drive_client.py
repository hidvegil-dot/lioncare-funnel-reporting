from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account


logger = logging.getLogger(__name__)

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


class GoogleDriveClient:
    def __init__(self, credentials_path: str) -> None:
        credentials = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=[DRIVE_SCOPE],
        )
        self._init_service(credentials)

    @classmethod
    def from_oauth_token(cls, token_path: str) -> "GoogleDriveClient":
        token_file = Path(token_path).expanduser()
        if not token_file.exists():
            raise FileNotFoundError(
                "Google Drive OAuth token file is missing. Run "
                "`python scripts/google_drive_oauth_init.py` first."
            )

        credentials = Credentials.from_authorized_user_file(str(token_file), scopes=[DRIVE_SCOPE])
        if not credentials.valid:
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
                token_file.write_text(credentials.to_json())
            else:
                raise ValueError(
                    "Google Drive OAuth token is invalid. Run "
                    "`python scripts/google_drive_oauth_init.py` again."
                )

        client = cls.__new__(cls)
        client._init_service(credentials)
        return client

    def _init_service(self, credentials: Any) -> None:
        self.service = build("drive", "v3", credentials=credentials, cache_discovery=False)

    def resolve_root_folder_id(self, root_folder_name: str) -> str:
        folder_name = root_folder_name.strip()
        if not folder_name:
            raise ValueError("Google Drive root folder name is empty")

        escaped_name = _escape_query_value(folder_name)
        query = (
            f"name = '{escaped_name}' and mimeType = '{FOLDER_MIME_TYPE}' "
            "and trashed = false"
        )
        response = (
            self.service.files()
            .list(
                q=query,
                spaces="drive",
                fields=(
                    "files(id,name,shared,capabilities/canAddChildren,"
                    "capabilities/canEdit,owners/emailAddress,webViewLink)"
                ),
                pageSize=20,
                corpora="user",
            )
            .execute()
        )
        folders = response.get("files", [])
        writable = [
            folder
            for folder in folders
            if folder.get("capabilities", {}).get("canAddChildren")
        ]
        if not writable:
            raise ValueError(
                "Google Drive root folder not found or not writable with the active "
                f"Drive credential: {folder_name}. Check that the OAuth user can access "
                "this normal My Drive folder and that the OAuth token was generated with "
                "the full Google Drive scope."
            )

        selected = next((folder for folder in writable if folder.get("shared")), writable[0])
        logger.info(
            "Resolved Google Drive root folder name=%s id=%s shared=%s",
            selected.get("name"),
            selected.get("id"),
            selected.get("shared"),
        )
        return str(selected["id"])

    def ensure_folder_path(self, path_parts: list[str], *, root_folder_id: str) -> str:
        parent_id = root_folder_id
        for folder_name in path_parts:
            parent_id = self.ensure_folder(folder_name, parent_id=parent_id)
        return parent_id

    def ensure_folder(self, name: str, *, parent_id: str) -> str:
        existing = self._find_folder(name=name, parent_id=parent_id)
        if existing:
            return str(existing["id"])

        logger.info("Creating Google Drive folder name=%s parent=%s", name, parent_id)
        created = (
            self.service.files()
            .create(
                body={
                    "name": name,
                    "mimeType": FOLDER_MIME_TYPE,
                    "parents": [parent_id],
                },
                fields="id,name,webViewLink",
            )
            .execute()
        )
        return str(created["id"])

    def upload_file(self, local_path: Path, *, folder_id: str, filename: str) -> str:
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=False)
        existing = self._find_file(name=filename, parent_id=folder_id)
        body: dict[str, Any] = {"name": filename}

        if existing:
            logger.info("Updating Google Drive file name=%s id=%s", filename, existing["id"])
            uploaded = (
                self.service.files()
                .update(
                    fileId=existing["id"],
                    body=body,
                    media_body=media,
                    fields="id,name,webViewLink",
                )
                .execute()
            )
        else:
            logger.info("Uploading Google Drive file name=%s folder=%s", filename, folder_id)
            body["parents"] = [folder_id]
            uploaded = (
                self.service.files()
                .create(body=body, media_body=media, fields="id,name,webViewLink")
                .execute()
            )
        return str(uploaded["id"])

    def get_file_link(self, file_id: str) -> str:
        file = self.service.files().get(fileId=file_id, fields="id,webViewLink").execute()
        return str(file.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view")

    def _find_folder(self, *, name: str, parent_id: str) -> dict[str, Any] | None:
        escaped_name = _escape_query_value(name)
        escaped_parent = _escape_query_value(parent_id)
        query = (
            f"name = '{escaped_name}' and mimeType = '{FOLDER_MIME_TYPE}' "
            f"and '{escaped_parent}' in parents and trashed = false"
        )
        return self._find_first(query)

    def _find_file(self, *, name: str, parent_id: str) -> dict[str, Any] | None:
        escaped_name = _escape_query_value(name)
        escaped_parent = _escape_query_value(parent_id)
        query = (
            f"name = '{escaped_name}' and '{escaped_parent}' in parents "
            "and trashed = false"
        )
        return self._find_first(query)

    def _find_first(self, query: str) -> dict[str, Any] | None:
        response = (
            self.service.files()
            .list(
                q=query,
                spaces="drive",
                fields="files(id,name)",
                pageSize=1,
                corpora="user",
            )
            .execute()
        )
        files = response.get("files", [])
        return files[0] if files else None


def _escape_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")
