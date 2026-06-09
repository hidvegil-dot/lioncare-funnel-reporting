from __future__ import annotations

import unittest
from datetime import date, datetime
from pathlib import Path

from ghl_client import GHLClient
from google_sheets_client import _column_letter
from parser import DAILY_REPORT_INDEX_COLUMNS, build_historical_rows
from report_builder import _evaluate_meta_adset, build_daily_decision_report
from scripts.check_daily_report_index import _daily_report_exists
from scripts import monitor_github_actions


REPO_ROOT = Path(__file__).resolve().parents[1]


class DailyReportAuditGuardTest(unittest.TestCase):
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
