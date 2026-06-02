from __future__ import annotations
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any
from urllib.parse import urlparse

import requests


EXPECTED_CUSTOM_FIELDS = [
    "lead_date",
    "first_booking_date",
    "show_date",
    "close_date",
    "lead_status",
]


logger = logging.getLogger(__name__)


class GHLAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class GHLConfig:
    api_key: str
    location_id: str
    base_url: str = "https://services.leadconnectorhq.com"
    api_version: str = "2021-07-28"
    page_limit: int = 100
    request_timeout_seconds: int = 30
    max_retries: int = 4
    retry_backoff_seconds: float = 1.5
    debug: bool = False

    @classmethod
    def from_env(cls) -> "GHLConfig":
        api_key = os.getenv("GHL_API_KEY", "").strip()
        location_id = os.getenv("GHL_LOCATION_ID", "").strip()
        if not api_key:
            raise ValueError("Missing GHL_API_KEY environment variable")
        if not location_id:
            raise ValueError("Missing GHL_LOCATION_ID environment variable")

        base_url = cls._normalize_base_url(os.getenv("GHL_BASE_URL", cls.base_url).strip() or cls.base_url)
        api_version = os.getenv("GHL_API_VERSION", cls.api_version).strip() or cls.api_version
        debug = os.getenv("GHL_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
        return cls(
            api_key=api_key,
            location_id=location_id,
            base_url=base_url,
            api_version=api_version,
            debug=debug,
        )

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        parsed = urlparse(base_url)
        if not parsed.scheme:
            base_url = f"https://{base_url}"
            parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"Invalid GHL_BASE_URL environment variable: {base_url!r}")
        return base_url.rstrip("/")


class GHLClient:
    def __init__(self, config: GHLConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {config.api_key}",
                "Version": config.api_version,
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        self._custom_field_map: dict[str, set[str]] | None = None

    def fetch_contacts_for_window(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        field_map = self.get_custom_field_map()
        contacts: list[dict[str, Any]] = []

        for contact in self.iter_contacts():
            normalized = self.normalize_contact(contact, field_map)
            if self._is_instagram_message_without_lead_data(normalized):
                continue
            if self._contact_matches_window(normalized, start_date, end_date):
                contacts.append(normalized)

        return contacts

    def fetch_all_contacts(self) -> list[dict[str, Any]]:
        field_map = self.get_custom_field_map()
        contacts: list[dict[str, Any]] = []

        for contact in self.iter_contacts():
            normalized = self.normalize_contact(contact, field_map)
            if self._is_instagram_message_without_lead_data(normalized):
                continue
            contacts.append(normalized)

        return contacts

    def fetch_closed_contact_meeting_counts(
        self,
        contacts: list[dict[str, Any]],
        start_date: date,
        end_date: date,
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for contact in contacts:
            close_date = contact.get("close_date")
            if not isinstance(close_date, date):
                continue
            if not (start_date <= close_date <= end_date):
                continue

            counts[contact["id"]] = self.count_meetings_for_contact(contact["id"])

        return counts

    def get_custom_field_map(self) -> dict[str, set[str]]:
        if self._custom_field_map is not None:
            return self._custom_field_map

        response = self._request(
            "GET",
            f"/locations/{self.config.location_id}/customFields",
        )
        payload = response.json()
        raw_fields = payload.get("customFields") or payload.get("fields") or payload.get("data") or []

        mapping = {name: {name} for name in EXPECTED_CUSTOM_FIELDS}
        for field in raw_fields:
            candidates = [
                field.get("name"),
                field.get("fieldKey"),
                field.get("key"),
                field.get("slug"),
                field.get("id"),
            ]
            normalized_candidates = {
                self._normalize_custom_field_identifier(candidate)
                for candidate in candidates
                if candidate
            }
            matched_names = [expected for expected in EXPECTED_CUSTOM_FIELDS if expected in normalized_candidates]
            for matched_name in matched_names:
                for candidate in candidates:
                    if candidate:
                        mapping[matched_name].add(str(candidate))

        if self.config.debug:
            print(
                "GHL DEBUG custom field map:",
                {key: sorted(value) for key, value in mapping.items()},
            )

        self._custom_field_map = mapping
        return mapping

    def iter_contacts(self) -> list[dict[str, Any]]:
        contacts: list[dict[str, Any]] = []
        page = 1

        while True:
            response = self._search_contacts_page(page=page)
            data = response.json()
            batch = data.get("contacts") or data.get("data") or data.get("results") or []
            if self.config.debug:
                print(f"GHL DEBUG contacts/search page={page} returned {len(batch)} contacts")
            if not batch:
                break

            contacts.extend(batch)
            if len(batch) < self.config.page_limit:
                break
            page += 1

        return contacts

    def _search_contacts_page(self, page: int) -> requests.Response:
        payload_variants = self._build_contact_search_payload_variants(page=page)
        last_error: GHLAPIError | None = None

        for payload in payload_variants:
            try:
                return self._request("POST", "/contacts/search", json=payload)
            except GHLAPIError as exc:
                last_error = exc
                if exc.status_code != 422:
                    raise

        raise last_error or GHLAPIError("Contacts search failed without a specific API error")

    def _build_contact_search_payload_variants(self, page: int) -> list[dict[str, Any]]:
        base_payload = {
            "locationId": self.config.location_id,
            "pageLimit": self.config.page_limit,
        }
        variants = [
            {
                **base_payload,
                "page": page,
            },
            {
                **base_payload,
                "page": page,
                "filters": [],
            },
        ]

        if page == 1:
            variants.append(dict(base_payload))

        return variants

    def normalize_contact(
        self,
        contact: dict[str, Any],
        field_map: dict[str, set[str]],
    ) -> dict[str, Any]:
        normalized = {
            "id": str(contact.get("id") or contact.get("_id") or ""),
            "name": " ".join(
                part for part in [contact.get("firstName"), contact.get("lastName")] if part
            ).strip(),
            "email": contact.get("email"),
            "phone": contact.get("phone"),
            # Kept intentionally for a future source/campaign breakdown without API refactoring.
            "source": contact.get("source"),
            "campaign": contact.get("campaign"),
            "landing_page_url": self._extract_landing_page_url(contact),
            "raw": contact,
        }

        for field_name in EXPECTED_CUSTOM_FIELDS:
            raw_value = self._extract_custom_field_value(contact, field_map[field_name])
            if field_name.endswith("_date"):
                normalized[field_name] = self._parse_date_value(raw_value)
            else:
                normalized[field_name] = raw_value

        return normalized

    def _is_instagram_message_without_lead_data(self, contact: dict[str, Any]) -> bool:
        """Exclude Instagram message-only contacts that are not real CRM leads."""
        if self._has_contact_identity(contact):
            return False
        if contact.get("landing_page_url"):
            return False

        raw = contact.get("raw") or {}
        attribution_values = []
        for key in ("attributionSource", "lastAttributionSource"):
            attribution = raw.get(key)
            if isinstance(attribution, dict):
                attribution_values.extend(
                    str(attribution.get(field) or "").strip().lower()
                    for field in ("sessionSource", "medium", "mediumId")
                )

        return "instagram" in attribution_values and "social media" in attribution_values

    def _has_contact_identity(self, contact: dict[str, Any]) -> bool:
        return bool(str(contact.get("email") or "").strip() or str(contact.get("phone") or "").strip())

    def count_meetings_for_contact(self, contact_id: str) -> int:
        appointments = self.fetch_contact_appointments(contact_id)

        showed_statuses = {
            "showed",
            "show",
            "completed",
            "confirmed-show",
            "attended",
            "attended_meeting",
        }
        showed_count = 0
        for appointment in appointments:
            status = str(
                appointment.get("appointmentStatus")
                or appointment.get("status")
                or appointment.get("calendarStatus")
                or ""
            ).strip().lower()
            if status in showed_statuses:
                showed_count += 1

        if showed_count > 0:
            return showed_count

        # Conservative fallback:
        # if the API does not expose a stable "showed" style status for this location,
        # we fall back to counting all appointments attached to the contact.
        return len(appointments)

    def fetch_contact_appointments(self, contact_id: str) -> list[dict[str, Any]]:
        response = self._request("GET", f"/contacts/{contact_id}/appointments")
        data = response.json()
        return data.get("appointments") or data.get("events") or data.get("data") or []

    def fetch_all_contact_appointments(self) -> list[dict[str, Any]]:
        appointments: list[dict[str, Any]] = []
        for contact in self.iter_contacts():
            contact_id = str(contact.get("id") or contact.get("_id") or "")
            if not contact_id:
                continue
            try:
                appointments.extend(self.fetch_contact_appointments(contact_id))
            except Exception:
                # Conservative fallback: skip individual contacts that cannot return appointments
                # so the weekly user report still completes.
                continue
        return appointments

    def fetch_appointments_for_contacts(
        self,
        contacts: list[dict[str, Any]],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        appointments: list[dict[str, Any]] = []
        seen_contact_ids: set[str] = set()

        for contact in contacts:
            contact_id = str(contact.get("id") or contact.get("_id") or "")
            if not contact_id or contact_id in seen_contact_ids:
                continue

            seen_contact_ids.add(contact_id)
            try:
                contact_appointments = self.fetch_contact_appointments(contact_id)
            except Exception:
                logger.warning("Skipping appointments for contact %s after API error", contact_id, exc_info=True)
                continue

            if start_date is None and end_date is None:
                appointments.extend(contact_appointments)
                continue

            for appointment in contact_appointments:
                appointment_date = self._extract_appointment_date(appointment)
                if appointment_date is None:
                    continue
                if start_date is not None and appointment_date < start_date:
                    continue
                if end_date is not None and appointment_date > end_date:
                    continue
                appointments.append(appointment)

        logger.info(
            "Collected %s appointments from %s relevant contacts",
            len(appointments),
            len(seen_contact_ids),
        )
        return appointments

    def fetch_calendar_events_for_users(
        self,
        user_ids: list[str],
        start_date: date,
        end_date: date,
        timezone_name: str = "Europe/Budapest",
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        tz = ZoneInfo(timezone_name)
        start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=tz)
        end_dt = datetime.combine(end_date, datetime.max.time(), tzinfo=tz)
        params_base = {
            "locationId": self.config.location_id,
            "startTime": str(int(start_dt.timestamp() * 1000)),
            "endTime": str(int(end_dt.timestamp() * 1000)),
        }

        for user_id in sorted({user_id.strip() for user_id in user_ids if user_id.strip()}):
            response = self._request(
                "GET",
                "/calendars/events",
                params={
                    **params_base,
                    "userId": user_id,
                },
            )
            data = response.json()
            user_events = data.get("events") or data.get("data") or []
            events.extend(user_events)

        logger.info(
            "Collected %s calendar events for %s users between %s and %s",
            len(events),
            len(set(user_ids)),
            start_date,
            end_date,
        )
        return events

    def fetch_opportunity(self, opportunity_id: str) -> dict[str, Any]:
        response = self._request("GET", f"/opportunities/{opportunity_id}")
        data = response.json()
        return data.get("opportunity") or data.get("data") or data

    def update_contact_custom_fields(self, contact_id: str, field_values: dict[str, Any]) -> dict[str, Any]:
        field_map = self.get_custom_field_map()
        custom_fields_payload = []

        for field_name, value in field_values.items():
            candidates = sorted(field_map.get(field_name, set()))
            field_id = next((candidate for candidate in candidates if not candidate.startswith("contact.")), None)
            if not field_id:
                raise ValueError(f"Could not resolve custom field id for {field_name}")
            custom_fields_payload.append({"id": field_id, "value": value})

        response = self._request(
            "PUT",
            f"/contacts/{contact_id}",
            json={"customFields": custom_fields_payload},
        )
        data = response.json()
        return data.get("contact") or data.get("data") or data

    def _contact_matches_window(self, contact: dict[str, Any], start_date: date, end_date: date) -> bool:
        for field_name in ("lead_date", "first_booking_date", "show_date", "close_date"):
            field_value = contact.get(field_name)
            if isinstance(field_value, date) and start_date <= field_value <= end_date:
                return True
        return False

    def _extract_custom_field_value(self, contact: dict[str, Any], accepted_keys: set[str]) -> Any:
        lowered_keys = {self._normalize_custom_field_identifier(key) for key in accepted_keys}
        custom_fields = contact.get("customFields") or contact.get("custom_fields") or []

        if isinstance(custom_fields, dict):
            for key, value in custom_fields.items():
                if self._normalize_custom_field_identifier(key) in lowered_keys:
                    return value

        if isinstance(custom_fields, list):
            for item in custom_fields:
                if not isinstance(item, dict):
                    continue
                identifier_candidates = [
                    item.get("id"),
                    item.get("fieldId"),
                    item.get("customFieldId"),
                    item.get("key"),
                    item.get("fieldKey"),
                    item.get("name"),
                ]
                if any(
                    self._normalize_custom_field_identifier(candidate) in lowered_keys
                    for candidate in identifier_candidates
                    if candidate
                ):
                    return item.get("value")

        for key, value in contact.items():
            if self._normalize_custom_field_identifier(key) in lowered_keys:
                return value

        return None

    def _extract_landing_page_url(self, contact: dict[str, Any]) -> str | None:
        attribution_candidates = [
            contact.get("attributionSource"),
            contact.get("lastAttributionSource"),
            contact.get("attributions"),
        ]
        for attribution in attribution_candidates:
            if isinstance(attribution, dict):
                raw_url = attribution.get("url")
                normalized = self._normalize_landing_url(raw_url)
                if normalized:
                    return normalized
            elif isinstance(attribution, list):
                for item in attribution:
                    if not isinstance(item, dict):
                        continue
                    raw_url = item.get("url")
                    normalized = self._normalize_landing_url(raw_url)
                    if normalized:
                        return normalized
        return None

    def _normalize_landing_url(self, raw_url: Any) -> str | None:
        if not raw_url:
            return None
        text = str(raw_url).strip()
        try:
            parsed = urlparse(text)
        except ValueError:
            return None
        if not parsed.scheme or not parsed.netloc or not parsed.path:
            return None
        path = parsed.path.rstrip("/") or "/"
        return f"{parsed.scheme}://{parsed.netloc}{path}"

    def _normalize_key(self, value: Any) -> str:
        return str(value).strip().lower().replace(" ", "_")

    def _normalize_custom_field_identifier(self, value: Any) -> str:
        normalized = self._normalize_key(value)
        if normalized.startswith("contact."):
            normalized = normalized.split(".", 1)[1]
        return normalized

    def _parse_date_value(self, raw_value: Any) -> date | None:
        if raw_value in (None, ""):
            return None
        if isinstance(raw_value, date) and not isinstance(raw_value, datetime):
            return raw_value
        if isinstance(raw_value, datetime):
            return raw_value.date()
        if isinstance(raw_value, (int, float)):
            # Some locations store date custom fields as epoch milliseconds.
            timestamp = raw_value / 1000 if raw_value > 10_000_000_000 else raw_value
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).date()

        raw_text = str(raw_value).strip()
        for candidate in (
            raw_text,
            raw_text[:10],
            raw_text.replace("Z", "+00:00"),
        ):
            try:
                return datetime.fromisoformat(candidate).date()
            except ValueError:
                continue

        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(raw_text, fmt).date()
            except ValueError:
                continue
        return None

    def _extract_appointment_date(self, appointment: dict[str, Any]) -> date | None:
        for key in ("startTime", "dateAdded", "endTime"):
            raw = appointment.get(key)
            if not raw:
                continue
            raw_text = str(raw).strip().replace("Z", "+00:00")
            candidates = [raw_text, raw_text.replace(" ", "T")]
            for candidate in candidates:
                try:
                    return date.fromisoformat(candidate[:10])
                except ValueError:
                    continue
        return None

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = f"{self.config.base_url.rstrip('/')}{path}"
        last_error: Exception | None = None
        request_payload = kwargs.get("json")

        if self.config.debug and request_payload is not None:
            print(f"GHL DEBUG request {method} {path} payload={request_payload}")

        for attempt in range(1, self.config.max_retries + 1):
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    timeout=self.config.request_timeout_seconds,
                    **kwargs,
                )
                if self.config.debug and 400 <= response.status_code < 500:
                    print(f"GHL DEBUG response {response.status_code} for {method} {path}: {response.text.strip()}")
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise requests.HTTPError(
                        f"Transient GHL API error: {response.status_code}",
                        response=response,
                    )
                response.raise_for_status()
                return response
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    break
                time.sleep(self.config.retry_backoff_seconds * attempt)

        status_code = getattr(getattr(last_error, "response", None), "status_code", None)
        raise GHLAPIError(
            f"GHL API request failed for {method} {path}: {last_error}",
            status_code=status_code,
        ) from last_error
