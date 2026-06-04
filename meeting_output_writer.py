from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from client_communication_ai import build_transcript_text, transcript_metadata
from google_drive_client import GoogleDriveClient
from meeting_dates import meeting_date_iso


MEETING_AI_FOLDERS = {
    "raw_transcript": "raw_transcripts",
    "crm_note": "crm_notes",
    "followup_draft": "followup_drafts",
    "diagnosis": "communication_diagnosis",
    "executive_summary": "executive_summaries",
    "weekly_patterns": "weekly_patterns",
}


@dataclass(frozen=True)
class MeetingOutputLinks:
    crm_note_link: str
    followup_draft_link: str
    diagnosis_link: str
    executive_summary_link: str
    transcript_link: str
    output_folder: str


class MeetingOutputWriter:
    def __init__(self, drive: GoogleDriveClient, root_folder_name: str) -> None:
        self.drive = drive
        self.root_folder_name = root_folder_name
        self.root_folder_id = drive.resolve_root_folder_id(root_folder_name)
        self.meeting_ai_folder_id = drive.ensure_folder_path(["meeting_ai"], root_folder_id=self.root_folder_id)
        self.folder_ids = {
            key: drive.ensure_folder_path(["meeting_ai", folder], root_folder_id=self.root_folder_id)
            for key, folder in MEETING_AI_FOLDERS.items()
        }

    def write_outputs(self, *, transcript: dict[str, Any], analysis: dict[str, Any]) -> MeetingOutputLinks:
        meeting_date = _date_prefix(transcript.get("date") or analysis.get("meeting_date"))
        client_slug = slugify(str(analysis.get("client_name") or "ismeretlen-ugyfel"))
        meeting_id = slugify(str(transcript.get("id") or "unknown"))
        base = f"{meeting_date}_{client_slug}_{meeting_id}"
        header = _output_header(analysis)

        crm_note_link = self._upload_markdown(
            folder_key="crm_note",
            filename=f"{base}_crm-note.md",
            content=f"{header}\n\n# CRM note\n\n{analysis.get('crm_note', '')}\n",
        )
        followup_link = self._upload_markdown(
            folder_key="followup_draft",
            filename=f"{base}_followup.md",
            content=f"{header}\n\n# Follow-up e-mail vázlat\n\n{analysis.get('followup_email', '')}\n",
        )
        diagnosis_link = self._upload_markdown(
            folder_key="diagnosis",
            filename=f"{base}_diagnosis.md",
            content=(
                f"{header}\n\n# Kommunikációs diagnózis\n\n"
                f"{analysis.get('communication_diagnosis', '')}\n\n"
                f"# Következő lépés javaslat\n\n{analysis.get('next_step_recommendation', '')}\n\n"
                f"# Strukturált mintázatok\n\n```json\n"
                f"{_json_dump(analysis.get('structured_patterns') or {})}\n```\n"
            ),
        )
        summary_link = self._upload_markdown(
            folder_key="executive_summary",
            filename=f"{base}_summary.md",
            content=f"{header}\n\n# Vezetői összefoglaló\n\n{analysis.get('executive_summary', '')}\n",
        )
        transcript_link = ""
        if os.getenv("RAW_TRANSCRIPT_SAVE_ENABLED", "true").strip().lower() not in {"0", "false", "no"}:
            transcript_link = self._upload_markdown(
                folder_key="raw_transcript",
                filename=f"{base}_transcript.md",
                content=(
                    f"{header}\n\n# Fireflies transcript\n\n"
                    f"```json\n{_json_dump(transcript_metadata(transcript))}\n```\n\n"
                    f"{build_transcript_text(transcript)}\n"
                ),
            )

        return MeetingOutputLinks(
            crm_note_link=crm_note_link,
            followup_draft_link=followup_link,
            diagnosis_link=diagnosis_link,
            executive_summary_link=summary_link,
            transcript_link=transcript_link,
            output_folder=f"{self.root_folder_name}/meeting_ai",
        )

    def _upload_markdown(self, *, folder_key: str, filename: str, content: str) -> str:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / filename
            path.write_text(content, encoding="utf-8")
            file_id = self.drive.upload_file(path, folder_id=self.folder_ids[folder_key], filename=filename)
            return self.drive.get_file_link(file_id)


def slugify(value: str) -> str:
    text = value.strip().lower()
    replacements = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ö": "o",
        "ő": "o",
        "ú": "u",
        "ü": "u",
        "ű": "u",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "ismeretlen"


def _date_prefix(value: Any) -> str:
    parsed = meeting_date_iso(value)
    return parsed or datetime.now().date().isoformat()


def _output_header(analysis: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"AI prompt verzió: {analysis.get('ai_prompt_version', '')}",
            f"Feldolgozási timestamp: {analysis.get('processed_at', '')}",
            f"Modell: {analysis.get('model', '')}",
            f"Workflow verzió: {analysis.get('workflow_version', '')}",
            f"Manual review flag: {analysis.get('manual_review_flag', '')}",
        ]
    )


def _json_dump(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)
