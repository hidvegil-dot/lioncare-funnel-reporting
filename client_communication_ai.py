from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

from meeting_dates import meeting_date_iso


PROMPT_VERSION = "meeting-client-communication-v2"
WORKFLOW_VERSION = "meeting-ai-v1"
DEFAULT_MODEL = "gpt-4o"
OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
LONG_TRANSCRIPT_CHAR_LIMIT = 28000


SYSTEM_PROMPT = """
Te a LionCare Meeting AI ügyfélkommunikációs elemzője vagy.

Feladatod NEM általános meeting summary. Feladatod értékesítést támogató, ügyfélkommunikációs és
döntéstámogatási anyag készítése egy pénzügyi tanácsadói konzultáció után.

Kötelező alapelvek:
- Magyarul dolgozz.
- Ne találj ki adatot. Ha valami nem hangzott el, pontosan ezt írd: "nem derült ki az átiratból".
- Különítsd el az ügyfél mondanivalóját, a tanácsadó kommunikációját, a döntési triggeret,
  a kifogást, a bizonytalanságot és a következő lépést.
- Minden fontos következtetéshez adj confidence szintet: magas, közepes vagy alacsony.
- A válasz legyen használható CRM-ben, follow-upban és belső sales döntésben.
- Ne legyen minimalista. A rövid, felszínes válasz hibás output.

Follow-up e-mail szabályok:
- Magyar nyelvű, magázó, professzionális, nem nyomulós.
- Kövesse az 5 lépcsős logikát: figyelem, keret, megértés, elfogadás, cselekvés.
- Ne használd a "köszönöm" szót.
- Ne használd a "pénzügyi rés" kifejezést.
- Ne állíts garantált hozamot.
- Ne írj pontos jövőbeni állami nyugdíj-kalkulációt.
- Konkrét számot csak akkor írj, ha ténylegesen elhangzott az átiratban.
- Az aláírás pontosan ez legyen:
Hidvégi László
pénzügyi tanácsadó
LionCare
+36 70 779 7726
MNB reg szám: 224052400166

Minimális tartalmi mélység:
- crm_note: legalább 12 sor, strukturált, GHL-be másolható.
- followup_email: legalább 7 bekezdés vagy blokk, ügyfélre szabott, a konkrét beszélgetésre épít.
- next_step_recommendation: legalább 7 konkrét pont.
- communication_diagnosis: legalább 8 konkrét megállapítás, külön "mi működött", "hol volt ellenállás",
  "mit kell jobban mondani", "zárási esély" logikával.
- executive_summary: legalább 6 konkrét pont vezetői nyelven.

Manual review flag:
- SEND_READY: ha az ügyfélnek küldhető anyag alacsony kockázatú.
- NEEDS_REVIEW: ha emberi átnézés kell.
- HIGH_RISK: ha megfelelőségi, adatminőségi vagy kommunikációs kockázat van.
"""


JSON_SCHEMA_HINT = {
    "client_name": "string",
    "client_identification_confidence": "magas|közepes|alacsony",
    "meeting_date": "string",
    "closing_probability": "1-10 number",
    "closing_probability_confidence": "magas|közepes|alacsony",
    "confidence_level": "magas|közepes|alacsony",
    "interest_level": "hideg|langyos|erős",
    "interest_level_confidence": "magas|közepes|alacsony",
    "main_goal": "string",
    "main_motivation": "string",
    "main_objection": "string",
    "main_red_flag": "string",
    "main_hot_trigger": "string",
    "decision_barrier": "string",
    "emotional_tone": "string",
    "next_action": "string",
    "next_action_confidence": "magas|közepes|alacsony",
    "recommended_status": "string",
    "priority": "LOW|NORMAL|HIGH PRIORITY",
    "crm_note": "detailed markdown string with fields: ügyfél neve, meeting dátuma, élethelyzet, fő pénzügyi cél, motiváció, félelem/ellenállás, döntési akadály, pénzügyi kapacitás, havi díjszint, érdeklődési szint, következő lépés, következő meeting, státuszjavaslat, confidence notes",
    "followup_email": "detailed Hungarian email draft using the 5-step sales logic and exact signature",
    "next_step_recommendation": "detailed markdown action plan: follow-up email, phone call, second meeting, new calculation, deadline, status, priority, owner action",
    "communication_diagnosis": "detailed markdown diagnosis: what worked, customer uncertainty, decision resistance, product timing, next step clarity, better wording, closing probability 1-10",
    "executive_summary": "detailed markdown CEO summary: client, meeting essence, decision state, business opportunity, main risk, next best step",
    "structured_patterns": {
        "main_objection": "string",
        "main_motivation": "string",
        "decision_barrier": "string",
        "closing_trigger": "string",
        "emotional_tone": "string"
    },
    "manual_review_flag": "SEND_READY|NEEDS_REVIEW|HIGH_RISK"
}


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str
    model: str = DEFAULT_MODEL

    @classmethod
    def from_env(cls) -> "OpenAIConfig":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("Missing OPENAI_API_KEY environment variable")
        return cls(api_key=api_key, model=os.getenv("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL)


class ClientCommunicationAI:
    def __init__(self, config: OpenAIConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            }
        )

    def analyze_meeting(self, transcript: dict[str, Any]) -> dict[str, Any]:
        if os.getenv("MEETING_AI_MOCK_OPENAI_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
            return build_mock_analysis(transcript, model=self.config.model)

        transcript_text = build_transcript_text(transcript)
        input_text = transcript_text
        if len(transcript_text) > LONG_TRANSCRIPT_CHAR_LIMIT:
            input_text = self._build_structured_digest(transcript, transcript_text)

        result = self._request_json(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Elemezd az alábbi Fireflies meeting átiratot ügyfélkommunikációs és sales "
                        "döntéstámogatási szempontból. Kizárólag JSON-t adj vissza. "
                        "Minden markdown mező legyen teljes, részletes, konkrétumokra épülő, nem minimalista. "
                        "A CRM note, follow-up, következő lépés, diagnózis és vezetői összefoglaló önmagában is használható anyag legyen. "
                        "Séma és tartalmi követelmények: "
                        f"{json.dumps(JSON_SCHEMA_HINT, ensure_ascii=False)}\n\n"
                        f"Meeting metaadatok:\n{json.dumps(transcript_metadata(transcript), ensure_ascii=False)}\n\n"
                        f"Átirat vagy strukturált kivonat:\n{input_text}"
                    ),
                },
            ]
        )
        if not _analysis_has_required_depth(result):
            result = self._expand_minimal_analysis(
                transcript=transcript,
                input_text=input_text,
                previous_result=result,
            )
        result.setdefault("client_name", "nem derült ki az átiratból")
        result.setdefault("meeting_date", meeting_date_iso(transcript.get("date")) or "nem derült ki az átiratból")
        result["ai_prompt_version"] = PROMPT_VERSION
        result["workflow_version"] = WORKFLOW_VERSION
        result["model"] = self.config.model
        result["processed_at"] = datetime.now().isoformat(timespec="seconds")
        return result

    def _expand_minimal_analysis(
        self,
        *,
        transcript: dict[str, Any],
        input_text: str,
        previous_result: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request_json(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Az előző válasz túl minimalista volt, ezért generáld újra teljes mélységben. "
                        "Ne rövidíts. Ne csak összefoglalj. Készíts sales használatra alkalmas anyagot. "
                        "Elvárt minimumok: CRM note legalább 250 szó; follow-up legalább 350 szó; "
                        "kommunikációs diagnózis legalább 500 szó; vezetői összefoglaló legalább 250 szó; "
                        "következő lépés javaslat legalább 150 szó. "
                        "Kizárólag JSON-t adj vissza ugyanazzal a sémával. "
                        f"Séma: {json.dumps(JSON_SCHEMA_HINT, ensure_ascii=False)}\n\n"
                        f"Előző, túl rövid válasz:\n{json.dumps(previous_result, ensure_ascii=False)}\n\n"
                        f"Meeting metaadatok:\n{json.dumps(transcript_metadata(transcript), ensure_ascii=False)}\n\n"
                        f"Átirat vagy strukturált kivonat:\n{input_text}"
                    ),
                },
            ]
        )

    def _build_structured_digest(self, transcript: dict[str, Any], transcript_text: str) -> str:
        digest = self._request_text(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Készíts tömör, strukturált kivonatot sales kommunikációs elemzéshez. "
                        "Őrizd meg a konkrét számokat, kifogásokat, célokat, döntési jeleket és következő lépéseket. "
                        f"Meeting metaadatok:\n{json.dumps(transcript_metadata(transcript), ensure_ascii=False)}\n\n"
                        f"Teljes átirat:\n{transcript_text}"
                    ),
                },
            ]
        )
        return digest

    def _request_json(self, input_messages: list[dict[str, str]]) -> dict[str, Any]:
        text = self._request_text(input_messages, response_format={"type": "json_object"})
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OpenAI response was not valid JSON: {text[:500]}") from exc

    def _request_text(
        self,
        input_messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
    ) -> str:
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": input_messages,
            "temperature": 0.2,
            "max_tokens": 8000,
        }
        if response_format:
            body["response_format"] = response_format
        response = self.session.post(OPENAI_CHAT_COMPLETIONS_URL, json=body, timeout=120)
        payload = response.json()
        if response.status_code >= 400:
            raise RuntimeError(f"OpenAI API error status={response.status_code}: {payload}")
        return _extract_response_text(payload)


def build_transcript_text(transcript: dict[str, Any]) -> str:
    sentences = transcript.get("sentences") or []
    if sentences:
        lines = []
        for sentence in sentences:
            speaker = str(sentence.get("speaker_name") or "Ismeretlen beszélő").strip()
            text = str(sentence.get("text") or "").strip()
            if text:
                lines.append(f"{speaker}: {text}")
        return "\n".join(lines)
    summary = transcript.get("summary") or {}
    return "\n".join(str(summary.get(key) or "") for key in ("overview", "short_summary", "action_items")).strip()


def transcript_metadata(transcript: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": transcript.get("id"),
        "title": transcript.get("title"),
        "date": meeting_date_iso(transcript.get("date")),
        "duration": transcript.get("duration"),
        "participants": transcript.get("participants"),
        "organizer_email": transcript.get("organizer_email"),
        "summary": transcript.get("summary"),
    }


def build_mock_analysis(transcript: dict[str, Any], *, model: str) -> dict[str, Any]:
    meeting_date = meeting_date_iso(transcript.get("date")) or "nem derült ki az átiratból"
    title = str(transcript.get("title") or "Fireflies meeting")
    client_name = _guess_client_name_from_title(title)
    processed_at = datetime.now().isoformat(timespec="seconds")
    return {
        "client_name": client_name,
        "client_identification_confidence": "alacsony",
        "meeting_date": meeting_date,
        "closing_probability": 0,
        "closing_probability_confidence": "alacsony",
        "confidence_level": "alacsony",
        "interest_level": "nem derült ki az átiratból",
        "interest_level_confidence": "alacsony",
        "main_goal": "nem derült ki az átiratból",
        "main_motivation": "nem derült ki az átiratból",
        "main_objection": "nem derült ki az átiratból",
        "main_red_flag": "MOCK teszt mód: valódi OpenAI elemzés nem futott.",
        "main_hot_trigger": "nem derült ki az átiratból",
        "decision_barrier": "nem derült ki az átiratból",
        "emotional_tone": "nem derült ki az átiratból",
        "next_action": "Éles OpenAI API keret aktiválása után újrafuttatni a meeting AI feldolgozást.",
        "next_action_confidence": "magas",
        "recommended_status": "NEEDS_REVIEW",
        "priority": "NORMAL",
        "crm_note": (
            f"Ügyfél neve: {client_name}\n"
            f"Meeting dátuma: {meeting_date}\n"
            "Élethelyzet: nem derült ki az átiratból\n"
            "Fő pénzügyi cél: nem derült ki az átiratból\n"
            "Fő motiváció: nem derült ki az átiratból\n"
            "Fő félelem / ellenállás: nem derült ki az átiratból\n"
            "Döntési akadály: nem derült ki az átiratból\n"
            "Pénzügyi kapacitás: nem derült ki az átiratból\n"
            "Javasolt havi díjszint: nem derült ki az átiratból\n"
            "Érdeklődési szint: nem derült ki az átiratból\n"
            "Következő lépés: éles OpenAI API kerettel újrafuttatni\n"
            "Státuszjavaslat: NEEDS_REVIEW\n"
            "\nMegjegyzés: ez technikai MOCK teszt output, nem ügyfélkommunikációs elemzés."
        ),
        "followup_email": (
            "Tárgy: Egyeztetés folytatása\n\n"
            "Tisztelt Ügyfelünk!\n\n"
            "A beszélgetés alapján a következő lépés pontosításához az anyag manuális átnézése szükséges.\n"
            "Ez a vázlat technikai teszt módban készült, ezért ügyfélnek nem küldhető.\n\n"
            "Hidvégi László\n"
            "pénzügyi tanácsadó\n"
            "LionCare\n"
            "+36 70 779 7726\n"
            "MNB reg szám: 224052400166"
        ),
        "next_step_recommendation": (
            "- Follow-up e-mail: éles elemzés után\n"
            "- Telefonhívás: nem derült ki az átiratból\n"
            "- Második kör időpont: nem derült ki az átiratból\n"
            "- Új kalkuláció: nem derült ki az átiratból\n"
            "- Határidő: OpenAI quota rendezése után azonnal\n"
            "- Prioritás: NORMAL\n"
            "- Státusz: NEEDS_REVIEW"
        ),
        "communication_diagnosis": (
            "MOCK teszt mód. A Fireflies, Drive és Sheet feldolgozási lánc ellenőrzésére készült. "
            "Valódi kommunikációs diagnózis csak aktív OpenAI API kerettel készül."
        ),
        "executive_summary": (
            f"Ügyfél: {client_name}\n"
            f"Meeting: {title}\n"
            "Állapot: technikai teszt sikeressége ellenőrizhető, üzleti elemzés még nem készült.\n"
            "Következő legjobb lépés: OpenAI API billing/credit aktiválása és újrafuttatás."
        ),
        "structured_patterns": {
            "main_objection": "nem derült ki az átiratból",
            "main_motivation": "nem derült ki az átiratból",
            "decision_barrier": "nem derült ki az átiratból",
            "closing_trigger": "nem derült ki az átiratból",
            "emotional_tone": "nem derült ki az átiratból",
        },
        "manual_review_flag": "NEEDS_REVIEW",
        "ai_prompt_version": f"{PROMPT_VERSION}-mock",
        "workflow_version": WORKFLOW_VERSION,
        "model": f"{model}-mock",
        "processed_at": processed_at,
    }


def _guess_client_name_from_title(title: str) -> str:
    cleaned = title.replace("Konzultáció", "").replace("konzultáció", "").strip(" -")
    return cleaned or "nem derült ki az átiratból"


def _extract_response_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "").strip()


def _analysis_has_required_depth(result: dict[str, Any]) -> bool:
    minimum_words = {
        "crm_note": 180,
        "followup_email": 240,
        "next_step_recommendation": 120,
        "communication_diagnosis": 300,
        "executive_summary": 160,
    }
    for field, minimum in minimum_words.items():
        if len(str(result.get(field) or "").split()) < minimum:
            return False
    return True
