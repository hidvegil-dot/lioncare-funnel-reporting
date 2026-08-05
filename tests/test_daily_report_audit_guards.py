from __future__ import annotations

import os
import unittest
from unittest import mock
from datetime import date, datetime
from pathlib import Path

from daily_split_exports import (
    DAILY_AD_PERFORMANCE_COLUMNS,
    LEAD_COHORT_COLUMNS,
    ExportValidationError,
    build_split_exports_from_daily_lead_rows,
    validate_lead_rows,
)
from ghl_client import GHLClient
from google_sheets_client import _column_letter
from parser import DAILY_REPORT_INDEX_COLUMNS, build_historical_rows
from report_builder import (
    DAILY_LEAD_CSV_COLUMNS,
    _build_current_crm_by_opportunity_owner_rows,
    _build_current_crm_by_owner_rows,
    _evaluate_meta_adset,
    build_daily_decision_report,
    build_daily_lead_csv_rows,
    write_daily_lead_csv_report,
)
from scripts.check_daily_report_index import _daily_report_exists, _fetch_daily_report_index_values
from scripts import monitor_github_actions
from scripts.update_opportunity_status_by_pipeline import select_pipeline


REPO_ROOT = Path(__file__).resolve().parents[1]


class DailyReportAuditGuardTest(unittest.TestCase):
    def test_opportunity_status_update_selects_pipeline_accent_insensitive(self) -> None:
        pipeline = select_pipeline(
            [
                {"id": "pipeline_1", "name": "Biztosítás 01"},
                {"id": "pipeline_2", "name": "Ügyfélszerződések"},
            ],
            "ugyfelszerzodesek",
        )

        self.assertEqual("pipeline_2", pipeline["id"])

    def test_ghl_window_uses_created_date_when_lead_date_is_missing(self) -> None:
        client = GHLClient.__new__(GHLClient)

        self.assertTrue(
            client._contact_matches_window(
                {"created_date": date(2026, 6, 4)},
                date(2026, 6, 4),
                date(2026, 6, 4),
            )
        )

    def test_webinar_adset_does_not_emit_landing_tracking_error(self) -> None:
        evaluation = _evaluate_meta_adset(
            funnel_type="webinar",
            spend=2500,
            link_click=12,
            landing_page_views=0,
            registration_leads=3,
        )

        self.assertIn("Webinár", evaluation)
        self.assertNotIn("landing hiba", evaluation.lower())
        self.assertNotIn("tracking", evaluation.lower())

    def test_webinar_uses_meta_form_leads_as_primary_meta_lead(self) -> None:
        report = build_daily_decision_report(
            report_date=date(2026, 6, 6),
            summary={
                "new_leads": 4,
                "booked_leads": 0,
                "showed_leads": 0,
                "closed_leads": 0,
            },
            ga4_data=None,
            meta_data={
                "summary": {
                    "spend": 8000,
                    "leads": 5,
                    "meta_form_leads": 5,
                    "registration_leads": 0,
                    "link_click": 0,
                    "landing_page_views": 0,
                },
                "adsets": [
                    {
                        "campaign_name": "Webinár instant form",
                        "adset_name": "Webinár teszt",
                        "spend": 8000,
                        "leads": 5,
                        "meta_form_leads": 5,
                        "registration_leads": 0,
                    }
                ],
            },
            contacts=[],
            current_crm_contacts=[],
        )

        self.assertEqual("webinar", report["funnel_type"])
        self.assertEqual(5, report["meta"]["leads"])
        self.assertEqual("Meta űrlap lead", report["meta"]["lead_label"])
        self.assertEqual(1600, report["calculated"]["meta_cpl"])

    def test_daily_report_breaks_down_ghl_leads_by_adset_utm(self) -> None:
        report = build_daily_decision_report(
            report_date=date(2026, 7, 24),
            summary={
                "new_leads": 2,
                "booked_leads": 0,
                "showed_leads": 0,
                "closed_leads": 0,
            },
            ga4_data=None,
            meta_data={
                "summary": {"spend": 1000, "registration_leads": 1},
                "adsets": [
                    {
                        "adset_id": "1201",
                        "adset_name": "LC+ szolgáltatók - CompleteRegistration",
                        "spend": 1000,
                        "registration_leads": 1,
                    }
                ],
            },
            contacts=[
                {
                    "lead_date": date(2026, 7, 24),
                    "landing_page_url": "https://lioncare.hu/landing-meta-nyugdij/",
                    "raw": {
                        "attributionSource": {
                            "url": "https://lioncare.hu/landing-meta-nyugdij/?utm_term=1201&utm_content=ad1"
                        }
                    },
                },
                {
                    "lead_date": date(2026, 7, 24),
                    "landing_page_url": "ismeretlen",
                    "raw": {"attributionSource": {"url": "https://lioncare.hu/webinar"}},
                },
            ],
            current_crm_contacts=[],
        )

        self.assertEqual(
            [
                {
                    "adset_id": "1201",
                    "adset_name": "LC+ szolgáltatók - CompleteRegistration",
                    "lead_count": 1,
                },
                {
                    "adset_id": "unknown",
                    "adset_name": "Ismeretlen / nincs UTM",
                    "lead_count": 1,
                },
            ],
            report["ghl"]["by_adset"],
        )

    def test_daily_report_tracks_lead_cohort_progress_without_duplicates(self) -> None:
        report = build_daily_decision_report(
            report_date=date(2026, 7, 29),
            summary={
                "new_leads": 2,
                "booked_leads": 0,
                "showed_leads": 0,
                "closed_leads": 0,
            },
            ga4_data=None,
            meta_data={"summary": {}, "adsets": []},
            contacts=[
                {"id": "contact_1", "name": "Lead One", "lead_date": date(2026, 7, 29), "raw": {}},
                {"id": "contact_2", "name": "Lead Two", "lead_date": date(2026, 7, 29), "raw": {}},
            ],
            current_crm_contacts=[],
            current_crm_appointments=[
                {
                    "contactId": "contact_1",
                    "startTime": "2026-07-31T10:00:00+02:00",
                    "dateAdded": "2026-07-29T12:00:00+02:00",
                    "appointmentStatus": "confirmed",
                },
                {
                    "contactId": "contact_1",
                    "startTime": "2026-08-02T10:00:00+02:00",
                    "dateAdded": "2026-07-30T12:00:00+02:00",
                    "appointmentStatus": "confirmed",
                },
                {
                    "contactId": "contact_2",
                    "startTime": "2026-07-29T10:00:00+02:00",
                    "dateAdded": "2026-07-29T08:00:00+02:00",
                    "appointmentStatus": "showed",
                },
            ],
        )

        cohort = report["ghl"]["lead_cohort"]
        self.assertEqual(2, cohort["total_leads"])
        self.assertEqual(2, cohort["booked_total"])
        self.assertEqual(1, cohort["showed_total"])
        self.assertEqual(2, cohort["new_bookings_on_report_date"])
        self.assertEqual(1, cohort["new_showed_on_report_date"])
        self.assertEqual(["Lead One", "Lead Two"], [row["name"] for row in cohort["rows"]])

    def test_daily_lead_csv_contains_stable_cohort_attribution_columns(self) -> None:
        rows = build_daily_lead_csv_rows(
            report_date=date(2026, 8, 5),
            contacts=[
                {
                    "id": "contact_1",
                    "name": "Lead One",
                    "email": "lead@example.com",
                    "phone": "06301234567",
                    "created_date": date(2026, 8, 5),
                    "lead_date": date(2026, 8, 5),
                    "close_date": date(2026, 8, 20),
                    "lead_status": "closed",
                    "source": "Facebook",
                    "landing_page_url": "https://lioncare.hu/webinar",
                    "raw": {
                        "attributionSource": {
                            "url": "https://lioncare.hu/webinar?utm_source=facebook&utm_campaign=Webinar&utm_term=adset_1&utm_content=ad_1"
                        }
                    },
                }
            ],
            appointments=[
                {
                    "contactId": "contact_1",
                    "startTime": "2026-08-06T18:00:00+02:00",
                    "dateAdded": "2026-08-05T12:00:00+02:00",
                    "appointmentStatus": "showed",
                }
            ],
            opportunities=[
                {
                    "id": "opp_1",
                    "contactId": "contact_1",
                    "status": "open",
                    "pipelineStageName": "Visszahívást kért",
                    "pipelineId": "pipeline_1",
                    "createdAt": "2026-08-05T12:10:00+02:00",
                    "updatedAt": "2026-08-05T12:20:00+02:00",
                }
            ],
            meta_data={
                "ads": [
                    {
                        "campaign_id": "campaign_1",
                        "campaign_name": "Webinar",
                        "adset_id": "adset_1",
                        "adset_name": "LC webinar adset",
                        "ad_id": "ad_1",
                        "ad_name": "LC webinar ad",
                        "spend": 1500.0,
                        "impressions": 1000,
                        "clicks": 80,
                        "link_click": 70,
                        "landing_page_views": 50,
                        "leads": 4,
                        "meta_form_leads": 4,
                        "registration_leads": 0,
                    }
                ],
                "adsets": [],
                "campaigns": [],
            },
            as_of_date=date(2026, 8, 7),
        )

        self.assertEqual(1, len(rows))
        self.assertEqual(DAILY_LEAD_CSV_COLUMNS, list(rows[0].keys()))
        self.assertEqual("contact_1", rows[0]["lead_id"])
        self.assertEqual("2026-08-05", rows[0]["lead_date"])
        self.assertEqual("Webinar", rows[0]["campaign_name"])
        self.assertEqual("adset_1", rows[0]["adset_id"])
        self.assertEqual("LC webinar ad", rows[0]["ad_name"])
        self.assertEqual("ad", rows[0]["meta_match_level"])
        self.assertEqual(1500.0, rows[0]["spend"])
        self.assertEqual(1000, rows[0]["impressions"])
        self.assertEqual(80, rows[0]["clicks"])
        self.assertEqual(50, rows[0]["landing_page_views"])
        self.assertEqual("showed", rows[0]["cohort_status"])
        self.assertEqual("2026-08-06", rows[0]["booking_date"])
        self.assertEqual("2026-08-06", rows[0]["showed_date"])
        self.assertEqual("2026-08-20", rows[0]["contract_date"])
        self.assertEqual("opp_1", rows[0]["opportunity_id"])
        self.assertEqual("open", rows[0]["opportunity_status"])

    def test_daily_lead_csv_writes_same_header_when_empty(self) -> None:
        csv_path = REPO_ROOT / ".tmp_daily_lead_csv_test.csv"
        try:
            write_daily_lead_csv_report(csv_path=csv_path, rows=[])
            header = csv_path.read_text(encoding="utf-8").splitlines()[0].split(",")
            self.assertEqual(DAILY_LEAD_CSV_COLUMNS, header)
        finally:
            if csv_path.exists():
                csv_path.unlink()

    def test_daily_split_exports_dedupe_ads_and_leads(self) -> None:
        result = build_split_exports_from_daily_lead_rows(
            report_date=date(2026, 8, 5),
            daily_lead_rows=[
                {
                    "lead_id": "lead_1",
                    "created_at": "2026-08-05T10:00:00+02:00",
                    "source": "Lioncare KATA",
                    "campaign_id": "120000000000000001",
                    "campaign_name": "Campaign",
                    "adset_id": "120000000000000002",
                    "adset_name": "Adset",
                    "ad_id": "120000000000000003",
                    "ad_name": "Ad",
                    "meta_match_level": "ad",
                    "booking_created_at": "2026-08-05T11:00:00+02:00",
                    "appointment_at": "2026-08-06T18:00:00+02:00",
                    "lead_status": "booked",
                },
                {
                    "lead_id": "lead_1",
                    "created_at": "2026-08-05T10:00:00+02:00",
                    "source": "Lioncare KATA",
                    "campaign_id": "120000000000000001",
                    "campaign_name": "Campaign",
                    "adset_id": "120000000000000002",
                    "adset_name": "Adset",
                    "ad_id": "120000000000000003",
                    "ad_name": "Ad",
                    "meta_match_level": "ad",
                    "lead_status": "booked",
                },
                {
                    "lead_id": "lead_2",
                    "created_at": "2026-08-05T12:00:00+02:00",
                    "source": "Lioncare KATA",
                    "meta_match_level": "none",
                    "lead_status": "new",
                },
            ],
            meta_data={
                "ads": [
                    {
                        "campaign_id": "120000000000000001",
                        "campaign_name": "Campaign",
                        "adset_id": "120000000000000002",
                        "adset_name": "Adset",
                        "ad_id": "120000000000000003",
                        "ad_name": "Ad",
                        "spend": 1200,
                        "impressions": 500,
                        "reach": 450,
                        "clicks": 20,
                        "link_click": 12,
                        "landing_page_views": 9,
                        "registration_leads": 2,
                    },
                    {
                        "campaign_id": "120000000000000001",
                        "campaign_name": "Campaign",
                        "adset_id": "120000000000000004",
                        "adset_name": "Adset zero",
                        "ad_id": "120000000000000005",
                        "ad_name": "Zero ad",
                        "spend": 0,
                        "impressions": 0,
                        "reach": 0,
                        "clicks": 0,
                        "link_click": 0,
                        "landing_page_views": 0,
                        "registration_leads": 0,
                    },
                ]
            },
            exported_at="2026-08-05T09:00:00+02:00",
            account_id="act_120000000000000000",
        )

        self.assertEqual(DAILY_AD_PERFORMANCE_COLUMNS, list(result["ad_rows"][0].keys()))
        self.assertEqual(2, len(result["ad_rows"]))
        self.assertEqual({"120000000000000003", "120000000000000005"}, {row["ad_id"] for row in result["ad_rows"]})
        self.assertEqual(1200, result["qa"]["meta_spend_huf"])
        self.assertEqual(LEAD_COHORT_COLUMNS, list(result["lead_rows"][0].keys()))
        self.assertEqual(2, len(result["lead_rows"]))
        self.assertEqual("lead_1:2", result["qa"]["duplicate_lead_ids"])
        lead_2 = [row for row in result["lead_rows"] if row["lead_id"] == "lead_2"][0]
        self.assertEqual("unattributed", lead_2["attribution_status"])
        self.assertEqual("", lead_2["ad_id"])
        self.assertEqual("datetime", result["lead_rows"][0]["lead_created_precision"])

    def test_daily_split_export_rejects_damaged_meta_id(self) -> None:
        with self.assertRaises(ExportValidationError):
            build_split_exports_from_daily_lead_rows(
                report_date=date(2026, 8, 5),
                daily_lead_rows=[],
                meta_data={
                    "ads": [
                        {
                            "campaign_id": "1.2000000000000001e+17",
                            "adset_id": "120000000000000002",
                            "ad_id": "120000000000000003",
                            "spend": 0,
                        }
                    ]
                },
                exported_at="2026-08-05T09:00:00+02:00",
                account_id="act_120000000000000000",
            )

    def test_daily_split_export_rejects_fake_midnight_timestamp(self) -> None:
        with self.assertRaises(ExportValidationError):
            validate_lead_rows(
                [
                    {
                        "lead_id": "lead_1",
                        "contact_id": "lead_1",
                        "lead_created_at": "2026-08-05T00:00:00+02:00",
                    }
                ]
            )

    def test_daily_report_index_contains_drive_links_and_funnel_type(self) -> None:
        rows = build_historical_rows(
            report_date=date(2026, 6, 4),
            summary={
                "new_leads": 13,
                "booked_leads": 4,
                "showed_leads": 2,
                "closed_leads": 0,
                "lead_to_booking_pct": 30.77,
                "booking_to_show_pct": 50,
                "show_to_close_pct": 0,
            },
            decision_report={
                "funnel_type": "landing",
                "meta": {"spend": 18000, "registration_leads": 12},
                "ghl": {"total_leads": 13, "unattributed_leads": 1, "current_crm_total": 356},
                "calculated": {"meta_cpl": 1500, "ghl_lead_cost": 1384.62},
                "diagnosis": {"daily_summary": "Teszt döntési összefoglaló"},
            },
            ga4_data=None,
            meta_data=None,
            report_links={"html": "https://drive/html", "csv": "https://drive/csv"},
            created_at=datetime(2026, 6, 4, 7, 16, 0),
        )

        self.assertEqual(len(DAILY_REPORT_INDEX_COLUMNS), len(rows["daily_report_index"][0]))
        index_row = dict(zip(DAILY_REPORT_INDEX_COLUMNS, rows["daily_report_index"][0]))
        self.assertEqual("2026-06-04", index_row["date"])
        self.assertEqual("https://drive/html", index_row["report_html_link"])
        self.assertEqual("https://drive/csv", index_row["report_csv_link"])
        self.assertEqual("landing", index_row["funnel_type"])
        self.assertEqual(13, index_row["ghl_leads"])

    def test_owner_rows_use_business_owner_labels(self) -> None:
        contacts = [
            {"lead_status": "new", "raw": {"assignedTo": "user_laci"}},
            {"lead_status": "booked", "raw": {"assignedTo": "user_amelita"}},
            {"lead_status": "showed", "raw": {"assignedTo": ""}},
        ]
        previous_labels = os.environ.get("GHL_USER_LABELS")
        os.environ["GHL_USER_LABELS"] = "user_laci:Hidvégi László,user_amelita:Gulyás Amelita"
        try:
            rows = _build_current_crm_by_owner_rows(contacts)
        finally:
            if previous_labels is None:
                os.environ.pop("GHL_USER_LABELS", None)
            else:
                os.environ["GHL_USER_LABELS"] = previous_labels

        labels = {row["owner_label"] for row in rows}
        self.assertEqual({"Én", "Amelita", "Unassigned"}, labels)

    def test_opportunity_owner_rows_count_open_opportunities(self) -> None:
        opportunities = [
            {"status": "open", "assignedTo": "user_laci", "pipelineId": "biztositas", "pipelineStageName": "Visszahívást kért"},
            {"status": "open", "assignedTo": "user_laci", "pipelineId": "biztositas", "pipelineStageName": "Nem vette fel 1"},
            {"status": "open", "assignedTo": "user_amelita", "pipelineId": "biztositas", "pipelineStageName": "Nem vette fel 2"},
            {"status": "open", "assignedTo": "", "pipelineId": "biztositas", "pipelineStageName": "Letöltötte"},
            {"status": "won", "assignedTo": "user_laci", "pipelineId": "biztositas", "pipelineStageName": "Lezárt"},
            {"status": "open", "assignedTo": "user_laci", "pipelineId": "masik", "pipelineStageName": "Másik pipeline"},
        ]
        previous_labels = os.environ.get("GHL_USER_LABELS")
        previous_pipeline = os.environ.get("GHL_OPPORTUNITY_PIPELINE_ID")
        os.environ["GHL_USER_LABELS"] = "user_laci:Hidvégi László,user_amelita:Gulyás Amelita"
        os.environ.pop("GHL_OPPORTUNITY_PIPELINE_ID", None)
        try:
            rows = _build_current_crm_by_opportunity_owner_rows(opportunities)
        finally:
            if previous_labels is None:
                os.environ.pop("GHL_USER_LABELS", None)
            else:
                os.environ["GHL_USER_LABELS"] = previous_labels
            if previous_pipeline is None:
                os.environ.pop("GHL_OPPORTUNITY_PIPELINE_ID", None)
            else:
                os.environ["GHL_OPPORTUNITY_PIPELINE_ID"] = previous_pipeline

        by_label = {row["owner_label"]: row for row in rows}
        self.assertEqual(2, by_label["Én"]["total"])
        self.assertEqual(1, by_label["Amelita"]["total"])
        self.assertEqual(1, by_label["Unassigned"]["total"])
        self.assertNotEqual(3, by_label["Én"]["total"])

    def test_daily_report_index_check_requires_drive_links(self) -> None:
        values = [
            ["date", "report_html_link", "report_csv_link"],
            ["2026-06-06", "https://drive/html", "https://drive/csv"],
        ]

        exists, reason, row = _daily_report_exists(values=values, report_date="2026-06-06")

        self.assertTrue(exists)
        self.assertEqual("daily_report_index row has Drive links", reason)
        self.assertEqual("https://drive/html", row["report_html_link"])

    def test_daily_report_index_check_treats_empty_links_as_missing(self) -> None:
        values = [
            ["date", "report_html_link", "report_csv_link"],
            ["2026-06-06", "", "https://drive/csv"],
        ]

        exists, reason, _ = _daily_report_exists(values=values, report_date="2026-06-06")

        self.assertFalse(exists)
        self.assertEqual("daily_report_index row has empty report_html_link", reason)

    def test_daily_report_index_check_can_accept_row_without_drive_links(self) -> None:
        values = [
            ["date", "report_html_link", "report_csv_link"],
            ["2026-06-06", "", ""],
        ]

        exists, reason, row = _daily_report_exists(
            values=values,
            report_date="2026-06-06",
            require_drive_links=False,
        )

        self.assertTrue(exists)
        self.assertEqual("daily_report_index row exists; Drive links not required", reason)
        self.assertEqual("2026-06-06", row["date"])

    def test_daily_report_index_fetch_retries_temporary_sheets_errors(self) -> None:
        class FakeResponse:
            status = 503

        class FakeHttpError(Exception):
            resp = FakeResponse()

        class FakeGetRequest:
            def __init__(self) -> None:
                self.calls = 0

            def execute(self) -> dict[str, list[list[str]]]:
                self.calls += 1
                if self.calls == 1:
                    raise FakeHttpError("temporary unavailable")
                return {"values": [["date"], ["2026-06-06"]]}

        class FakeValues:
            def __init__(self, request: FakeGetRequest) -> None:
                self.request = request

            def get(self, **_: object) -> FakeGetRequest:
                return self.request

        class FakeSpreadsheets:
            def __init__(self, request: FakeGetRequest) -> None:
                self.request = request

            def values(self) -> FakeValues:
                return FakeValues(self.request)

        class FakeService:
            def __init__(self, request: FakeGetRequest) -> None:
                self.request = request

            def spreadsheets(self) -> FakeSpreadsheets:
                return FakeSpreadsheets(self.request)

        request = FakeGetRequest()
        with mock.patch("scripts.check_daily_report_index.HttpError", FakeHttpError):
            values = _fetch_daily_report_index_values(
                service=FakeService(request),
                spreadsheet_id="sheet",
                attempts=2,
                base_sleep_seconds=0,
            )

        self.assertEqual([["date"], ["2026-06-06"]], values)
        self.assertEqual(2, request.calls)

    def test_daily_workflow_skips_inactive_schedule_guard(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/daily_funnel_report.yml").read_text(encoding="utf-8")

        self.assertIn("Skip inactive schedule guard", workflow)
        self.assertIn("::notice title=Inactive daily schedule::", workflow)
        self.assertNotIn("Fail inactive schedule guard", workflow)
        self.assertIn("steps.budapest_time.outputs.run_report != 'true'", workflow)

    def test_monitor_ignores_explicit_inactive_guard_run(self) -> None:
        def fake_github_json(*, repo: str, token: str, path: str) -> dict:
            if path.endswith("/runs/1/jobs?per_page=100"):
                return {
                    "jobs": [
                        {
                            "steps": [
                                {"name": "Budapest 06:30 guard", "conclusion": "success"},
                                {"name": "Fail inactive schedule guard", "conclusion": "failure"},
                                {"name": "Run daily report", "conclusion": "skipped"},
                            ]
                        }
                    ]
                }
            raise AssertionError(path)

        original = monitor_github_actions._github_json
        monitor_github_actions._github_json = fake_github_json
        try:
            warnings: list[str] = []
            selected = monitor_github_actions._latest_meaningful_run(
                repo="repo/name",
                token="",
                check=monitor_github_actions.CHECKS[0],
                warnings=warnings,
                runs=[
                    {"id": 1, "event": "schedule", "status": "completed", "conclusion": "failure"},
                    {"id": 2, "event": "workflow_dispatch", "status": "completed", "conclusion": "success"},
                ],
            )
        finally:
            monitor_github_actions._github_json = original

        self.assertEqual(2, selected["id"])
        self.assertTrue(any("inactive schedule guard" in warning for warning in warnings))

    def test_monitor_ignores_clean_inactive_guard_run(self) -> None:
        def fake_github_json(*, repo: str, token: str, path: str) -> dict:
            if path.endswith("/runs/1/jobs?per_page=100"):
                return {
                    "jobs": [
                        {
                            "steps": [
                                {"name": "Budapest Monday 07:00 guard", "conclusion": "success"},
                                {"name": "Run weekly GHL report", "conclusion": "skipped"},
                            ]
                        }
                    ]
                }
            raise AssertionError(path)

        original = monitor_github_actions._github_json
        monitor_github_actions._github_json = fake_github_json
        try:
            warnings: list[str] = []
            selected = monitor_github_actions._latest_meaningful_run(
                repo="repo/name",
                token="",
                check=monitor_github_actions.CHECKS[1],
                warnings=warnings,
                runs=[
                    {"id": 1, "event": "schedule", "status": "completed", "conclusion": "success"},
                    {"id": 2, "event": "workflow_dispatch", "status": "completed", "conclusion": "success"},
                ],
            )
        finally:
            monitor_github_actions._github_json = original

        self.assertEqual(2, selected["id"])
        self.assertTrue(any("inactive schedule guard" in warning for warning in warnings))

    def test_column_letter_supports_more_than_z(self) -> None:
        self.assertEqual("A", _column_letter(1))
        self.assertEqual("Z", _column_letter(26))
        self.assertEqual("AA", _column_letter(27))
        self.assertEqual("AB", _column_letter(28))


if __name__ == "__main__":
    unittest.main()
