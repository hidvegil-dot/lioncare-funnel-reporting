from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path
from typing import Any

import requests


logger = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"


class OneDriveClient:
    def __init__(
        self,
        *,
        client_id: str,
        refresh_token: str,
        client_secret: str | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.access_token = self._refresh_access_token()

    @classmethod
    def from_env_optional(cls) -> "OneDriveClient | None":
        client_id = os.getenv("ONEDRIVE_CLIENT_ID", "").strip()
        refresh_token = os.getenv("ONEDRIVE_REFRESH_TOKEN", "").strip()
        client_secret = os.getenv("ONEDRIVE_CLIENT_SECRET", "").strip() or None
        if not client_id or not refresh_token:
            return None
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
        )

    def upload_file(self, local_path: Path, *, remote_path: str) -> str:
        normalized_path = _normalize_remote_path(remote_path)
        mime_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": mime_type,
        }
        logger.info("Uploading OneDrive file path=%s", normalized_path)
        response = requests.put(
            f"{GRAPH_BASE_URL}/me/drive/root:/{normalized_path}:/content",
            headers=headers,
            data=local_path.read_bytes(),
            timeout=60,
        )
        response.raise_for_status()
        return str(response.json().get("id", ""))

    def ensure_folder_path(self, remote_path: str) -> None:
        parent_path = ""
        for part in _split_remote_path(remote_path):
            current_path = "/".join([parent_path, part]).strip("/")
            if not self._folder_exists(current_path):
                self._create_folder(parent_path=parent_path, name=part)
            parent_path = current_path

    def _refresh_access_token(self) -> str:
        payload: dict[str, str] = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "scope": "offline_access Files.ReadWrite",
        }
        if self.client_secret:
            payload["client_secret"] = self.client_secret
        response = requests.post(TOKEN_URL, data=payload, timeout=30)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise ValueError("Microsoft token refresh response did not include access_token")
        return str(access_token)

    def _folder_exists(self, remote_path: str) -> bool:
        response = requests.get(
            f"{GRAPH_BASE_URL}/me/drive/root:/{_normalize_remote_path(remote_path)}",
            headers={"Authorization": f"Bearer {self.access_token}"},
            timeout=30,
        )
        if response.status_code == 404:
            return False
        response.raise_for_status()
        item: dict[str, Any] = response.json()
        return "folder" in item

    def _create_folder(self, *, parent_path: str, name: str) -> str:
        parent_endpoint = (
            f"{GRAPH_BASE_URL}/me/drive/root/children"
            if not parent_path
            else f"{GRAPH_BASE_URL}/me/drive/root:/{_normalize_remote_path(parent_path)}:/children"
        )
        response = requests.post(
            parent_endpoint,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            },
            json={
                "name": name,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "replace",
            },
            timeout=30,
        )
        response.raise_for_status()
        return str(response.json().get("id", ""))


def _split_remote_path(remote_path: str) -> list[str]:
    return [part.strip() for part in remote_path.replace("\\", "/").split("/") if part.strip()]


def _normalize_remote_path(remote_path: str) -> str:
    return "/".join(_split_remote_path(remote_path))
