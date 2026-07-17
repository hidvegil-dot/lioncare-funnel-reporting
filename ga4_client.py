from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

from google.analytics.data_v1alpha import AlphaAnalyticsDataClient
from google.analytics.data_v1alpha.types import (
    DateRange as AlphaDateRange,
    Funnel,
    FunnelFieldFilter,
    FunnelFilterExpression,
    FunnelStep,
    RunFunnelReportRequest,
    StringFilter as AlphaStringFilter,
)
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest
from google.oauth2 import service_account


GA4_READONLY_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"


@dataclass(frozen=True)
class GA4Config:
    property_id: str
    credentials_path: str

    @classmethod
    def from_env_optional(cls) -> "GA4Config | None":
        property_id = os.getenv("GA4_PROPERTY_ID", "").strip()
        credentials_path = os.getenv("GA4_CREDENTIALS_PATH", "").strip()
        if not property_id or not credentials_path:
            return None
        return cls(property_id=property_id, credentials_path=credentials_path)


class GA4Client:
    def __init__(self, config: GA4Config) -> None:
        self.config = config
        credentials = service_account.Credentials.from_service_account_file(
            config.credentials_path,
            scopes=[GA4_READONLY_SCOPE],
        )
        self.client = BetaAnalyticsDataClient(credentials=credentials)
        self.alpha_client = AlphaAnalyticsDataClient(credentials=credentials)

    def fetch_summary_and_daily_rows(self, start_date: date, end_date: date) -> dict[str, Any]:
        summary_response = self.client.run_report(
            RunReportRequest(
                property=f"properties/{self.config.property_id}",
                dimensions=[],
                metrics=[
                    Metric(name="sessions"),
                    Metric(name="totalUsers"),
                    Metric(name="newUsers"),
                    Metric(name="screenPageViews"),
                    Metric(name="keyEvents"),
                ],
                date_ranges=[
                    DateRange(
                        start_date=start_date.isoformat(),
                        end_date=end_date.isoformat(),
                    )
                ],
            )
        )

        summary_row = summary_response.rows[0] if summary_response.rows else None
        summary = {
            "sessions": _metric_int(summary_row, 0),
            "total_users": _metric_int(summary_row, 1),
            "new_users": _metric_int(summary_row, 2),
            "page_views": _metric_int(summary_row, 3),
            "key_events": _metric_int(summary_row, 4),
        }

        summary["sessions_per_user"] = round(
            summary["sessions"] / summary["total_users"], 2
        ) if summary["total_users"] else 0.0

        daily_response = self.client.run_report(
            RunReportRequest(
                property=f"properties/{self.config.property_id}",
                dimensions=[Dimension(name="date")],
                metrics=[
                    Metric(name="sessions"),
                    Metric(name="totalUsers"),
                    Metric(name="newUsers"),
                    Metric(name="screenPageViews"),
                    Metric(name="keyEvents"),
                ],
                date_ranges=[
                    DateRange(
                        start_date=start_date.isoformat(),
                        end_date=end_date.isoformat(),
                    )
                ],
                order_bys=[{"dimension": {"dimension_name": "date"}}],
            )
        )

        daily_rows = [
            {
                "date": _parse_ga4_date(row.dimension_values[0].value),
                "sessions": _metric_int(row, 0),
                "total_users": _metric_int(row, 1),
                "new_users": _metric_int(row, 2),
                "page_views": _metric_int(row, 3),
                "key_events": _metric_int(row, 4),
            }
            for row in daily_response.rows
        ]

        landing_funnel = self.fetch_landing_page_funnel(start_date=start_date, end_date=end_date)
        landing_performance = self.fetch_landing_page_performance(start_date=start_date, end_date=end_date)

        return {
            "summary": summary,
            "rows": daily_rows,
            "landing_funnel": landing_funnel,
            "landing_performance": landing_performance,
        }

    def fetch_landing_page_performance(self, start_date: date, end_date: date) -> dict[str, Any]:
        landing_configs = [
            {
                "landing_url": "https://lioncare.hu/landing-meta-nyugdij/",
                "booking_url": "https://lioncare.hu/foglalas/",
                "thank_you_url": "https://lioncare.hu/koszonjuk-meta-nyugdij/",
            },
        ]

        rows = []
        for config in landing_configs:
            landing_url = config["landing_url"]
            total_users = self._fetch_page_metric(
                start_date=start_date,
                end_date=end_date,
                page_location=landing_url,
                metric_name="totalUsers",
            )
            sessions = self._fetch_page_metric(
                start_date=start_date,
                end_date=end_date,
                page_location=landing_url,
                metric_name="sessions",
            )
            engagement_duration = self._fetch_page_metric_float(
                start_date=start_date,
                end_date=end_date,
                page_location=landing_url,
                metric_name="userEngagementDuration",
            )
            engaged_sessions = self._fetch_page_metric(
                start_date=start_date,
                end_date=end_date,
                page_location=landing_url,
                metric_name="engagedSessions",
            )
            booking_users = 0
            thank_you_users = 0
            landing_users = 0

            if config["booking_url"]:
                booking_funnel_rows = self._run_two_step_page_funnel(
                    start_date=start_date,
                    end_date=end_date,
                    first_path=landing_url,
                    second_path=config["booking_url"],
                    first_name="Landing page",
                    second_name="Booking page",
                )
                booking_users = sum(step_counts.get("step_2", 0) for step_counts in booking_funnel_rows.values())
                landing_users = max(landing_users, sum(step_counts.get("step_1", 0) for step_counts in booking_funnel_rows.values()))

            if config["thank_you_url"]:
                thank_you_funnel_rows = self._run_two_step_page_funnel(
                    start_date=start_date,
                    end_date=end_date,
                    first_path=landing_url,
                    second_path=config["thank_you_url"],
                    first_name="Landing page",
                    second_name="Thank-you page",
                )
                thank_you_users = sum(step_counts.get("step_2", 0) for step_counts in thank_you_funnel_rows.values())
                landing_users = max(landing_users, sum(step_counts.get("step_1", 0) for step_counts in thank_you_funnel_rows.values()))

            if landing_users == 0:
                landing_users = total_users

            rows.append(
                {
                    "landing_url": landing_url,
                    "page_view": self._fetch_page_metric(
                        start_date=start_date,
                        end_date=end_date,
                        page_location=landing_url,
                        metric_name="screenPageViews",
                    ),
                    "users": landing_users,
                    "total_users": total_users,
                    "sessions": sessions,
                    "average_engagement_time": round(engagement_duration / landing_users, 2) if landing_users else 0.0,
                    "engaged_sessions": engaged_sessions,
                    "engagement_rate": round((engaged_sessions / sessions) * 100, 2) if sessions else 0.0,
                    "booking_page_view": self._fetch_page_metric(
                        start_date=start_date,
                        end_date=end_date,
                        page_location=config["booking_url"],
                        metric_name="screenPageViews",
                    ) if config["booking_url"] else 0,
                    "booking_users": booking_users,
                    "thank_you_page_view": self._fetch_page_metric(
                        start_date=start_date,
                        end_date=end_date,
                        page_location=config["thank_you_url"],
                        metric_name="screenPageViews",
                    ) if config["thank_you_url"] else 0,
                    "thank_you_users": thank_you_users,
                }
            )

        return {
            "rows": rows,
            "summary": {
                "page_view": sum(row["page_view"] for row in rows),
                "users": sum(row["users"] for row in rows),
                "booking_page_view": sum(row["booking_page_view"] for row in rows),
                "booking_users": sum(row["booking_users"] for row in rows),
                "thank_you_page_view": sum(row["thank_you_page_view"] for row in rows),
                "thank_you_users": sum(row["thank_you_users"] for row in rows),
            },
        }

    def fetch_landing_page_funnel(self, start_date: date, end_date: date) -> dict[str, Any]:
        landing_path = "https://lioncare.hu/landing-meta-nyugdij/"
        booking_path = "https://lioncare.hu/foglalas/"
        thank_you_path = "https://lioncare.hu/koszonjuk-meta-nyugdij/"

        booking_rows = self._run_two_step_page_funnel(
            start_date=start_date,
            end_date=end_date,
            first_path=landing_path,
            second_path=booking_path,
            first_name="Landing page",
            second_name="Booking page",
        )
        thank_you_rows = self._run_two_step_page_funnel(
            start_date=start_date,
            end_date=end_date,
            first_path=landing_path,
            second_path=thank_you_path,
            first_name="Landing page",
            second_name="Thank-you page",
        )

        merged_by_date: dict[str, dict[str, int]] = {}
        for raw_date, users in booking_rows.items():
            merged_by_date.setdefault(raw_date, {})["landing_users"] = users.get("step_1", 0)
            merged_by_date[raw_date]["booking_users"] = users.get("step_2", 0)
        for raw_date, users in thank_you_rows.items():
            merged_by_date.setdefault(raw_date, {})["landing_users"] = users.get("step_1", 0)
            merged_by_date[raw_date]["thank_you_users"] = users.get("step_2", 0)

        daily_rows: list[dict[str, Any]] = []
        landing_total = 0
        booking_total = 0
        thank_you_total = 0
        for raw_date in sorted(merged_by_date):
            landing_users = merged_by_date[raw_date].get("landing_users", 0)
            booking_users = merged_by_date[raw_date].get("booking_users", 0)
            thank_you_users = merged_by_date[raw_date].get("thank_you_users", 0)
            landing_total += landing_users
            booking_total += booking_users
            thank_you_total += thank_you_users
            daily_rows.append(
                {
                    "date": raw_date,
                    "landing_users": landing_users,
                    "booking_users": booking_users,
                    "thank_you_users": thank_you_users,
                    "landing_to_booking_pct": _percent(booking_users, landing_users),
                    "landing_to_thank_you_pct": _percent(thank_you_users, landing_users),
                }
            )

        return {
            "landing_path": landing_path,
            "booking_path": booking_path,
            "thank_you_path": thank_you_path,
            "summary": {
                "landing_users": landing_total,
                "booking_users": booking_total,
                "thank_you_users": thank_you_total,
                "landing_to_booking_pct": _percent(booking_total, landing_total),
                "landing_to_thank_you_pct": _percent(thank_you_total, landing_total),
            },
            "rows": daily_rows,
        }

    def _run_two_step_page_funnel(
        self,
        *,
        start_date: date,
        end_date: date,
        first_path: str,
        second_path: str,
        first_name: str,
        second_name: str,
    ) -> dict[str, dict[str, int]]:
        response = self.alpha_client.run_funnel_report(
            RunFunnelReportRequest(
                property=f"properties/{self.config.property_id}",
                date_ranges=[
                    AlphaDateRange(
                        start_date=start_date.isoformat(),
                        end_date=end_date.isoformat(),
                    )
                ],
                funnel=Funnel(
                    is_open_funnel=False,
                    steps=[
                        FunnelStep(
                            name=first_name,
                            filter_expression=_page_path_filter(first_path),
                        ),
                        FunnelStep(
                            name=second_name,
                            filter_expression=_page_path_filter(second_path),
                        ),
                    ],
                ),
                funnel_visualization_type=RunFunnelReportRequest.FunnelVisualizationType.TRENDED_FUNNEL,
            )
        )

        headers = [header.name for header in response.funnel_visualization.dimension_headers]
        step_index = headers.index("funnelStepName")
        date_index = headers.index("date")
        rows_by_date: dict[str, dict[str, int]] = {}

        for row in response.funnel_visualization.rows:
            raw_step_name = row.dimension_values[step_index].value
            raw_date = _parse_ga4_date(row.dimension_values[date_index].value)
            if raw_step_name.startswith("1."):
                step_key = "step_1"
            elif raw_step_name.startswith("2."):
                step_key = "step_2"
            else:
                continue
            rows_by_date.setdefault(raw_date, {})[step_key] = _metric_int(row, 0)

        return rows_by_date

    def _fetch_page_metric(
        self,
        *,
        start_date: date,
        end_date: date,
        page_location: str | None,
        metric_name: str,
    ) -> int:
        if not page_location:
            return 0
        host_name, page_path = _split_url(page_location)
        response = self.client.run_report(
            RunReportRequest(
                property=f"properties/{self.config.property_id}",
                metrics=[Metric(name=metric_name)],
                date_ranges=[
                    DateRange(
                        start_date=start_date.isoformat(),
                        end_date=end_date.isoformat(),
                    )
                ],
                dimension_filter=_and_exact_filters(
                    {
                        "hostName": host_name,
                        "pagePath": page_path,
                    }
                ),
            )
        )
        row = response.rows[0] if response.rows else None
        return _metric_int(row, 0)

    def _fetch_page_metric_float(
        self,
        *,
        start_date: date,
        end_date: date,
        page_location: str | None,
        metric_name: str,
    ) -> float:
        if not page_location:
            return 0.0
        host_name, page_path = _split_url(page_location)
        response = self.client.run_report(
            RunReportRequest(
                property=f"properties/{self.config.property_id}",
                metrics=[Metric(name=metric_name)],
                date_ranges=[
                    DateRange(
                        start_date=start_date.isoformat(),
                        end_date=end_date.isoformat(),
                    )
                ],
                dimension_filter=_and_exact_filters(
                    {
                        "hostName": host_name,
                        "pagePath": page_path,
                    }
                ),
            )
        )
        row = response.rows[0] if response.rows else None
        return _metric_float(row, 0)


def _metric_int(row: Any, index: int) -> int:
    if row is None:
        return 0
    raw = row.metric_values[index].value
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return 0


def _metric_float(row: Any, index: int) -> float:
    if row is None:
        return 0.0
    raw = row.metric_values[index].value
    try:
        return round(float(raw), 2)
    except (TypeError, ValueError):
        return 0.0


def _parse_ga4_date(raw: str) -> str:
    return datetime.strptime(raw, "%Y%m%d").date().isoformat()


def _page_path_filter(path: str) -> FunnelFilterExpression:
    normalized_location = _normalize_page_location(path)
    return FunnelFilterExpression(
        funnel_field_filter=FunnelFieldFilter(
            field_name="pageLocation",
            string_filter=AlphaStringFilter(
                value=normalized_location,
                match_type=AlphaStringFilter.MatchType.BEGINS_WITH,
                case_sensitive=False,
            ),
        )
    )


def _percent(part: int, whole: int) -> float:
    if not whole:
        return 0.0
    return round((part / whole) * 100, 1)


def _split_url(raw_url: str) -> tuple[str, str]:
    parsed = urlparse(raw_url)
    host_name = parsed.netloc.lower()
    page_path = parsed.path or "/"
    return host_name, page_path


def _and_exact_filters(field_values: dict[str, str]) -> dict[str, Any]:
    return {
        "and_group": {
            "expressions": [
                {
                    "filter": {
                        "field_name": field_name,
                        "string_filter": {
                            "value": field_value,
                            "match_type": "EXACT",
                            "case_sensitive": False,
                        },
                    }
                }
                for field_name, field_value in field_values.items()
                if field_value
            ]
        }
    }


def _normalize_page_location(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    host_name = parsed.netloc.lower()
    page_path = parsed.path or "/"
    return f"{parsed.scheme or 'https'}://{host_name}{page_path}"
