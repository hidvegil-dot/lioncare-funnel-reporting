from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any

import requests

from funnel_filters import filter_meta_rows


META_API_VERSION = "v22.0"


class MetaAdsAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class MetaAdsConfig:
    access_token: str
    ad_account_id: str
    campaign_id: str | None = None
    api_version: str = META_API_VERSION

    @classmethod
    def from_env_optional(cls) -> "MetaAdsConfig | None":
        access_token = os.getenv("META_ACCESS_TOKEN", "").strip()
        ad_account_id = os.getenv("META_AD_ACCOUNT_ID", "").strip()
        campaign_id = os.getenv("META_CAMPAIGN_ID", "").strip() or None
        if not access_token or not ad_account_id:
            return None
        return cls(
            access_token=access_token,
            ad_account_id=ad_account_id,
            campaign_id=campaign_id,
            api_version=os.getenv("META_API_VERSION", META_API_VERSION).strip() or META_API_VERSION,
        )


class MetaAdsClient:
    def __init__(self, config: MetaAdsConfig) -> None:
        self.config = config
        self.session = requests.Session()

    def fetch_summary_and_breakdowns(self, start_date: date, end_date: date) -> dict[str, Any]:
        campaign_rows = filter_meta_rows(
            self._fetch_insights(level="campaign", start_date=start_date, end_date=end_date)
        )
        adset_rows = filter_meta_rows(
            self._fetch_insights(level="adset", start_date=start_date, end_date=end_date)
        )
        ad_rows = filter_meta_rows(
            self._fetch_insights(level="ad", start_date=start_date, end_date=end_date)
        )

        summary = _summarize_meta_rows(campaign_rows)

        return {
            "summary": summary,
            "campaigns": [_normalize_meta_row(row, "campaign") for row in campaign_rows],
            "adsets": [_normalize_meta_row(row, "adset") for row in adset_rows],
            "ads": [_normalize_meta_row(row, "ad") for row in ad_rows],
        }

    def _fetch_insights(self, *, level: str, start_date: date, end_date: date) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "access_token": self.config.access_token,
            "level": level,
            "action_report_time": "conversion",
            "fields": ",".join(
                [
                    "campaign_id",
                    "campaign_name",
                    "adset_id",
                    "adset_name",
                    "ad_id",
                    "ad_name",
                    "date_start",
                    "date_stop",
                    "spend",
                    "impressions",
                    "clicks",
                    "ctr",
                    "cpc",
                    "actions",
                    "action_values",
                    "cost_per_action_type",
                ]
            ),
            "time_range": f'{{"since":"{start_date.isoformat()}","until":"{end_date.isoformat()}"}}',
            "limit": 200,
        }
        if self.config.campaign_id:
            params["filtering"] = f'[{{"field":"campaign.id","operator":"EQUAL","value":"{self.config.campaign_id}"}}]'

        url = f"https://graph.facebook.com/{self.config.api_version}/{self.config.ad_account_id}/insights"
        rows: list[dict[str, Any]] = []

        while url:
            response = self.session.get(url, params=params if "graph.facebook.com" in url else None, timeout=30)
            if not response.ok:
                try:
                    payload = response.json()
                except ValueError:
                    payload = {"raw": response.text[:1000]}
                raise MetaAdsAPIError(
                    f"Meta Ads API error {response.status_code}: {payload}"
                )
            payload = response.json()
            rows.extend(payload.get("data", []))
            paging = payload.get("paging", {})
            url = paging.get("next")
            params = None

        return rows


def _summarize_meta_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        "spend": 0.0,
        "impressions": 0,
        "clicks": 0,
        "link_click": 0,
        "landing_page_views": 0,
        "leads": 0,
        "meta_form_leads": 0,
        "registration_leads": 0,
        "schedule_events": 0,
        "contact_events": 0,
        "purchase_events": 0,
        "registration_value": 0.0,
        "schedule_value": 0.0,
        "contact_value": 0.0,
        "purchase_value": 0.0,
    }
    for row in rows:
        normalized = _normalize_meta_row(row, "campaign")
        for key in totals:
            totals[key] += normalized[key]

    totals["ctr"] = round((totals["link_click"] / totals["impressions"]) * 100, 2) if totals["impressions"] else 0.0
    totals["cpc"] = round(totals["spend"] / totals["link_click"], 2) if totals["link_click"] else 0.0
    totals["cost_per_lead"] = round(totals["spend"] / totals["leads"], 2) if totals["leads"] else 0.0
    totals["cost_per_meta_form_lead"] = (
        round(totals["spend"] / totals["meta_form_leads"], 2) if totals["meta_form_leads"] else 0.0
    )
    totals["cost_per_landing_page_view"] = (
        round(totals["spend"] / totals["landing_page_views"], 2) if totals["landing_page_views"] else 0.0
    )
    return totals


def _normalize_meta_row(row: dict[str, Any], level: str) -> dict[str, Any]:
    actions = _action_lookup(row.get("actions"))
    action_values = _action_value_lookup(row.get("action_values"))
    registration_leads = _max_action(
        actions,
        "complete_registration",
        "omni_complete_registration",
        "offsite_complete_registration_add_meta_leads",
        "offsite_conversion.fb_pixel_complete_registration",
    )
    schedule_events = _max_action(
        actions,
        "schedule",
        "omni_schedule",
        "offsite_conversion.fb_pixel_schedule",
    )
    contact_events = _max_action(
        actions,
        "contact",
        "omni_contact",
        "offsite_conversion.fb_pixel_contact",
    )
    purchase_events = _max_action(
        actions,
        "purchase",
        "omni_purchase",
        "offsite_conversion.fb_pixel_purchase",
    )
    result = {
        "campaign_id": row.get("campaign_id"),
        "campaign_name": row.get("campaign_name"),
        "adset_id": row.get("adset_id"),
        "adset_name": row.get("adset_name"),
        "ad_id": row.get("ad_id"),
        "ad_name": row.get("ad_name"),
        "name": (
            row.get(f"{level}_name")
            or row.get("campaign_name")
            or row.get("adset_name")
            or row.get("ad_name")
            or level
        ),
        "spend": _to_float(row.get("spend")),
        "impressions": _to_int(row.get("impressions")),
        "clicks": _to_int(row.get("clicks")),
        "link_click": actions.get("link_click", 0),
        "ctr": _to_float(row.get("ctr")),
        "cpc": _to_float(row.get("cpc")),
        "landing_page_views": actions.get("landing_page_view", 0),
        "leads": actions.get("lead", 0),
        "meta_form_leads": actions.get("lead", 0),
        "registration_leads": registration_leads,
        "schedule_events": schedule_events,
        "contact_events": contact_events,
        "purchase_events": purchase_events,
        "registration_value": _max_action_value(
            action_values,
            "complete_registration",
            "omni_complete_registration",
            "offsite_complete_registration_add_meta_leads",
            "offsite_conversion.fb_pixel_complete_registration",
        ),
        "schedule_value": _max_action_value(
            action_values,
            "schedule",
            "omni_schedule",
            "offsite_conversion.fb_pixel_schedule",
        ),
        "contact_value": _max_action_value(
            action_values,
            "contact",
            "omni_contact",
            "offsite_conversion.fb_pixel_contact",
        ),
        "purchase_value": _max_action_value(
            action_values,
            "purchase",
            "omni_purchase",
            "offsite_conversion.fb_pixel_purchase",
        ),
    }
    result["cost_per_lead"] = round(result["spend"] / result["leads"], 2) if result["leads"] else 0.0
    result["cost_per_meta_form_lead"] = (
        round(result["spend"] / result["meta_form_leads"], 2) if result["meta_form_leads"] else 0.0
    )
    return result


def _max_action(actions: dict[str, int], *action_types: str) -> int:
    return max((actions.get(action_type, 0) for action_type in action_types), default=0)


def _max_action_value(action_values: dict[str, float], *action_types: str) -> float:
    return round(max((action_values.get(action_type, 0.0) for action_type in action_types), default=0.0), 2)


def _action_lookup(actions: Any) -> dict[str, int]:
    lookup: dict[str, int] = {}
    if not isinstance(actions, list):
        return lookup
    for item in actions:
        if not isinstance(item, dict):
            continue
        action_type = str(item.get("action_type") or "").strip()
        if not action_type:
            continue
        lookup[action_type] = _to_int(item.get("value"))
    return lookup


def _action_value_lookup(action_values: Any) -> dict[str, float]:
    lookup: dict[str, float] = {}
    if not isinstance(action_values, list):
        return lookup
    for item in action_values:
        if not isinstance(item, dict):
            continue
        action_type = str(item.get("action_type") or "").strip()
        if not action_type:
            continue
        lookup[action_type] = _to_float(item.get("value"))
    return lookup


def _to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0
