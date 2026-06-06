from __future__ import annotations

import unittest
from datetime import date, datetime
from pathlib import Path

from ghl_client import GHLClient
from parser import DAILY_REPORT_INDEX_COLUMNS, build_historical_rows
from report_builder import _evaluate_meta_adset


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

    def test_daily_workflow_fails_inactive_schedule_guard(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/daily_funnel_report.yml").read_text(encoding="utf-8")

        self.assertIn("Fail inactive schedule guard", workflow)
        self.assertIn("exit 1", workflow)
        self.assertIn("steps.budapest_time.outputs.run_report != 'true'", workflow)


if __name__ == "__main__":
    unittest.main()
