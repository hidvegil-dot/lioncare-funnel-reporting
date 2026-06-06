from __future__ import annotations

import os
import unittest
from datetime import date

from funnel_filters import filter_funnel_contacts, filter_meta_rows, infer_funnel_type_from_meta_row


class FunnelFilterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_patterns = os.environ.get("REPORT_EXCLUDED_LEAD_PATTERNS")
        self.previous_end_date = os.environ.get("REPORT_EXCLUDED_LEAD_END_DATE")
        os.environ["REPORT_EXCLUDED_LEAD_PATTERNS"] = "webinar,webinár"
        os.environ["REPORT_EXCLUDED_LEAD_END_DATE"] = "2026-05-16"

    def tearDown(self) -> None:
        self._restore_env("REPORT_EXCLUDED_LEAD_PATTERNS", self.previous_patterns)
        self._restore_env("REPORT_EXCLUDED_LEAD_END_DATE", self.previous_end_date)

    def test_filters_webinar_contacts_until_cutoff(self) -> None:
        contacts = [
            {
                "id": "event",
                "lead_date": date(2026, 5, 16),
                "raw": {"attributionSource": {"url": "https://lioncare.hu/webinar?utm_campaign=webinar"}},
            },
            {
                "id": "funnel",
                "lead_date": date(2026, 5, 16),
                "raw": {"attributionSource": {"url": "https://lioncare.hu/landing-meta-nyugdij/"}},
            },
            {
                "id": "future-webinar-word",
                "lead_date": date(2026, 5, 17),
                "raw": {"attributionSource": {"url": "https://lioncare.hu/webinar"}},
            },
        ]

        self.assertEqual(["funnel", "future-webinar-word"], [row["id"] for row in filter_funnel_contacts(contacts)])

    def test_filters_webinar_meta_rows(self) -> None:
        rows = [
            {"campaign_name": "Webinár event 05.16", "spend": "1000", "date_start": "2026-05-16"},
            {"campaign_name": "LC+ szolgáltatók", "adset_name": "SKÁLÁZD ÓVATOSAN", "spend": "2000"},
        ]

        self.assertEqual(["LC+ szolgáltatók"], [row["campaign_name"] for row in filter_meta_rows(rows)])

    def test_webinar_meta_rows_are_not_excluded_after_cutoff_date(self) -> None:
        rows = [
            {"campaign_name": "Webinár event utókövetés", "spend": "1000", "date_start": "2026-05-17"},
            {"campaign_name": "Webinár event 05.16", "spend": "1000", "date_start": "2026-05-16"},
        ]

        self.assertEqual(["Webinár event utókövetés"], [row["campaign_name"] for row in filter_meta_rows(rows)])

    def test_infers_webinar_funnel_type_without_cutoff_filtering(self) -> None:
        self.assertEqual("webinar", infer_funnel_type_from_meta_row({"campaign_name": "Webinár instant form"}))
        self.assertEqual("landing", infer_funnel_type_from_meta_row({"campaign_name": "LC+ szolgáltatók"}))

    def _restore_env(self, key: str, value: str | None) -> None:
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
