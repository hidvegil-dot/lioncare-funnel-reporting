import argparse
import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from email_sender import EmailConfig, send_report_email
from ga4_client import GA4Client, GA4Config
from ghl_client import GHLClient, GHLConfig
from meta_ads_client import MetaAdsClient, MetaAdsConfig
from report_storage import persist_daily_report_history
from report_builder import (
    build_comparison_quick_snapshot,
    build_daily_decision_report,
    build_ga4_landing_comparison,
    build_landing_lead_cards,
    build_period_ghl_status_comparison,
    overlay_funnel_counts_from_appointments,
    build_period_comparison,
    build_report_rows,
    build_user_meeting_comparison,
    build_weekly_comparison,
    summarize_period,
    write_comparison_pdf,
    write_csv_report,
    write_html_report,
    write_weekly_comparison_csv,
    write_weekly_comparison_html,
)


logger = logging.getLogger(__name__)


def env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def parse_args() -> argparse.Namespace:
    today = date.today()
    default_end = previous_business_day(today)
    default_start = default_end

    parser = argparse.ArgumentParser(
        description="Generate a daily funnel report from GoHighLevel contacts and appointments."
    )
    parser.add_argument(
        "--report-type",
        choices=["daily", "weekly_compare", "monthly_compare", "period_compare"],
        default="daily",
        help="`daily` creates a date-window report. `weekly_compare` compares the current reporting week through yesterday against the preceding 7 days. `monthly_compare` compares the last completed business month vs the one before. `period_compare` compares your chosen start/end interval against the immediately preceding interval of the same length.",
    )
    parser.add_argument(
        "--start-date",
        default=default_start.isoformat(),
        help="Inclusive start date in YYYY-MM-DD format. Defaults to the last 30 days.",
    )
    parser.add_argument(
        "--end-date",
        default=default_end.isoformat(),
        help="Inclusive end date in YYYY-MM-DD format. Defaults to today.",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory where CSV and HTML reports will be written.",
    )
    parser.add_argument(
        "--send-email",
        action="store_true",
        help="Send the generated report by email using REPORT_* SMTP settings from the environment.",
    )
    return parser.parse_args()


def parse_iso_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def resolve_date_window(report_type: str, start_raw: str, end_raw: str) -> tuple[date, date]:
    if report_type == "weekly_compare":
        return compute_last_two_reporting_weeks_window(today=date.today())
    if report_type == "monthly_compare":
        default_daily = previous_business_day(date.today()).isoformat()
        if start_raw == default_daily and end_raw == default_daily:
            return compute_last_two_business_months_window(today=date.today())[0:2]
        return parse_iso_date(start_raw), parse_iso_date(end_raw)
    if report_type == "daily":
        previous_workday = previous_business_day(date.today())
        default_raw = previous_workday.isoformat()
        if start_raw == default_raw and end_raw == default_raw:
            return previous_workday, previous_workday
        start_date = parse_iso_date(start_raw)
        end_date = parse_iso_date(end_raw)
        return start_date, end_date

    start_date = parse_iso_date(start_raw)
    end_date = parse_iso_date(end_raw)
    return start_date, end_date


def compute_last_two_reporting_weeks_window(today: date) -> tuple[date, date]:
    end_date = today - timedelta(days=1)
    start_date = end_date - timedelta(days=13)
    return start_date, end_date


def previous_business_day(today: date) -> date:
    candidate = today - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def add_months(base: date, months: int) -> date:
    year = base.year + ((base.month - 1 + months) // 12)
    month = ((base.month - 1 + months) % 12) + 1
    return date(year, month, base.day)


def compute_last_two_business_months_window(today: date) -> tuple[date, date, date]:
    this_month_boundary = date(today.year, today.month, 15)
    if today >= this_month_boundary:
        next_unfinished_start = this_month_boundary
    else:
        next_unfinished_start = add_months(this_month_boundary, -1)

    current_period_start = add_months(next_unfinished_start, -1)
    previous_period_start = add_months(next_unfinished_start, -2)
    current_period_end = next_unfinished_start - timedelta(days=1)
    return previous_period_start, current_period_end, current_period_start


def month_end(day: date) -> date:
    return add_months(date(day.year, day.month, 1), 1) - timedelta(days=1)


def load_report_user_ids() -> list[str]:
    user_ids: list[str] = []
    for pair in os.getenv("GHL_USER_LABELS", "").split(","):
        if ":" not in pair:
            continue
        user_id = pair.split(":", 1)[0].strip()
        if user_id:
            user_ids.append(user_id)
    return user_ids


def main() -> None:
    load_dotenv()
    args = parse_args()
    strict_data = env_flag("REPORT_DATA_STRICT")

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    configure_run_logging(output_dir=output_dir)
    run_started_at = time.perf_counter()

    try:
        start_date, end_date = resolve_date_window(
            report_type=args.report_type,
            start_raw=args.start_date,
            end_raw=args.end_date,
        )
        if start_date > end_date:
            raise ValueError("start-date must be earlier than or equal to end-date")

        fetch_start_date = start_date
        comparison_current_start: date | None = None
        comparison_previous_start: date | None = None
        comparison_previous_end: date | None = None

        if args.report_type == "period_compare":
            period_days = (end_date - start_date).days + 1
            comparison_current_start = start_date
            comparison_previous_end = start_date - timedelta(days=1)
            comparison_previous_start = comparison_previous_end - timedelta(days=period_days - 1)
            fetch_start_date = comparison_previous_start

        logger.info(
            "Starting report run: type=%s start=%s end=%s output_dir=%s",
            args.report_type,
            fetch_start_date,
            end_date,
            output_dir,
        )

        config = GHLConfig.from_env()
        client = GHLClient(config)

        contacts_started_at = time.perf_counter()
        contacts = client.fetch_contacts_for_window(start_date=fetch_start_date, end_date=end_date)
        logger.info(
            "Fetched %s contacts in %.2fs",
            len(contacts),
            time.perf_counter() - contacts_started_at,
        )

        meetings_started_at = time.perf_counter()
        closed_meeting_counts = client.fetch_closed_contact_meeting_counts(
            contacts=contacts,
            start_date=fetch_start_date,
            end_date=end_date,
        )
        logger.info(
            "Fetched closed-meeting counts for %s contacts in %.2fs",
            len(closed_meeting_counts),
            time.perf_counter() - meetings_started_at,
        )

        rows = build_report_rows(
            contacts=contacts,
            closed_meeting_counts=closed_meeting_counts,
            start_date=fetch_start_date,
            end_date=end_date,
        )
        summary = summarize_period(rows=rows, closed_meeting_counts=closed_meeting_counts)
        logger.info("Built %s daily rows", len(rows))
        landing_lead_cards = build_landing_lead_cards(
            contacts=contacts,
            start_date=start_date,
            end_date=end_date,
        )
        current_crm_contacts: list[dict[str, object]] | None = None
        if args.report_type == "daily":
            full_contacts_started_at = time.perf_counter()
            current_crm_contacts = client.fetch_all_contacts()
            logger.info(
                "Fetched %s full CRM contacts in %.2fs",
                len(current_crm_contacts),
                time.perf_counter() - full_contacts_started_at,
            )
            daily_appointments_started_at = time.perf_counter()
            # Daily booking/show metrics must reflect real calendar activity, not only
            # custom field dates on leads created inside the reporting window.
            daily_appointments = client.fetch_appointments_for_contacts(
                contacts=current_crm_contacts,
                start_date=start_date,
                end_date=end_date,
            )
            rows = overlay_funnel_counts_from_appointments(rows=rows, appointments=daily_appointments)
            summary = summarize_period(rows=rows, closed_meeting_counts=closed_meeting_counts)
            logger.info(
                "Overlayed daily booking/show counts from %s appointments in %.2fs",
                len(daily_appointments),
                time.perf_counter() - daily_appointments_started_at,
            )

        ga4_data: dict[str, object] | None = None
        meta_data: dict[str, object] | None = None
        ga4_config = GA4Config.from_env_optional()
        if ga4_config is not None and args.report_type in {"daily", "weekly_compare", "monthly_compare"}:
            ga4_started_at = time.perf_counter()
            try:
                ga4_client = GA4Client(ga4_config)
                ga4_data = ga4_client.fetch_summary_and_daily_rows(
                    start_date=start_date,
                    end_date=end_date,
                )
                logger.info("Fetched GA4 daily summary in %.2fs", time.perf_counter() - ga4_started_at)
            except Exception:
                logger.exception(
                    "Skipping GA4 data after %.2fs because the GA4 query failed",
                    time.perf_counter() - ga4_started_at,
                )
                if strict_data:
                    raise
        meta_config = MetaAdsConfig.from_env_optional()
        if meta_config is not None and args.report_type in {"daily", "weekly_compare", "monthly_compare"}:
            meta_started_at = time.perf_counter()
            try:
                meta_client = MetaAdsClient(meta_config)
                meta_data = meta_client.fetch_summary_and_breakdowns(
                    start_date=start_date,
                    end_date=end_date,
                )
                logger.info("Fetched Meta Ads summary in %.2fs", time.perf_counter() - meta_started_at)
            except Exception:
                logger.exception(
                    "Skipping Meta Ads data after %.2fs because the Meta query failed",
                    time.perf_counter() - meta_started_at,
                )
                if strict_data:
                    raise
        decision_report = None
        if args.report_type == "daily":
            decision_report = build_daily_decision_report(
                report_date=start_date,
                summary=summary,
                ga4_data=ga4_data,
                meta_data=meta_data,
                contacts=contacts,
                current_crm_contacts=current_crm_contacts or contacts,
            )

        if args.report_type == "weekly_compare":
            current_period_start = date.fromisoformat(rows[7]["date"])
            previous_period_start = date.fromisoformat(rows[0]["date"])
            week_code = f"CW{end_date.isocalendar().week:02d}"
            weekly_appointments_started_at = time.perf_counter()
            # Use full appointment history up to the report end date so
            # 2nd/3rd meeting ordinals remain correct even when the first
            # showed meeting happened before the comparison window.
            relevant_appointments = client.fetch_appointments_for_contacts(
                contacts=contacts,
                end_date=end_date,
            )
            user_calendar_events = client.fetch_calendar_events_for_users(
                user_ids=load_report_user_ids(),
                start_date=previous_period_start,
                end_date=end_date,
            )
            rows = overlay_funnel_counts_from_appointments(rows=rows, appointments=relevant_appointments)
            comparison = build_weekly_comparison(rows=rows)
            if ga4_data and ga4_data.get("landing_funnel"):
                comparison["ga4_landing"] = build_ga4_landing_comparison(
                    landing_rows=ga4_data["landing_funnel"]["rows"],
                    current_period_start=current_period_start,
                    current_label=f"{rows[7]['date']} to {rows[-1]['date']}",
                    previous_label=f"{rows[0]['date']} to {rows[6]['date']}",
                )
            comparison["ghl_status_comparison"] = build_period_ghl_status_comparison(
                contacts=contacts,
                previous_start=previous_period_start,
                previous_end=current_period_start - timedelta(days=1),
                current_start=current_period_start,
                current_end=end_date,
            )
            current_meta_data = None
            if meta_config is not None:
                current_meta_data = MetaAdsClient(meta_config).fetch_summary_and_breakdowns(
                    start_date=current_period_start,
                    end_date=end_date,
                )
            comparison["quick_snapshot"] = build_comparison_quick_snapshot(
                current_summary=comparison["current_period"]["summary"],
                meta_summary=(current_meta_data or {}).get("summary"),
            )
            user_meeting_comparison = build_user_meeting_comparison(
                appointments=user_calendar_events or relevant_appointments,
                previous_start=previous_period_start,
                current_start=current_period_start,
                current_end=end_date,
                ordinal_appointments=relevant_appointments,
            )
            logger.info(
                "Built weekly user meeting comparison from %s calendar events and %s contact appointments in %.2fs",
                len(user_calendar_events),
                len(relevant_appointments),
                time.perf_counter() - weekly_appointments_started_at,
            )
            csv_path = output_dir / "weekly_funnel_report.csv"
            html_path = output_dir / "weekly_funnel_report.html"
            pdf_path = output_dir / f"LionCare weekly report {week_code}.pdf"
            write_weekly_comparison_csv(csv_path=csv_path, comparison=comparison)
            write_weekly_comparison_html(
                html_path=html_path,
                comparison=comparison,
                start_date=start_date,
                end_date=end_date,
                title=f"LionCare heti riport {week_code}",
                user_meeting_comparison=user_meeting_comparison,
                meta_data=current_meta_data,
            )
            write_comparison_pdf(
                pdf_path=pdf_path,
                comparison=comparison,
                title=f"LionCare heti riport {week_code}",
                start_date=start_date,
                end_date=end_date,
                user_meeting_comparison=user_meeting_comparison,
            )
        elif args.report_type == "monthly_compare":
            default_daily = previous_business_day(date.today()).isoformat()
            using_default_monthly_window = args.start_date == default_daily and args.end_date == default_daily
            if using_default_monthly_window:
                _, _, current_period_start = compute_last_two_business_months_window(today=date.today())
                previous_period_end = current_period_start - timedelta(days=1)
                previous_period_start = fetch_start_date
            else:
                current_period_start = start_date
                previous_period_end = start_date - timedelta(days=1)
                if start_date.day == 1 and end_date == month_end(start_date):
                    previous_period_start = add_months(start_date, -1)
                else:
                    period_days = (end_date - start_date).days + 1
                    previous_period_start = previous_period_end - timedelta(days=period_days - 1)
            monthly_appointments_started_at = time.perf_counter()
            # Use full appointment history up to the report end date so
            # 2nd/3rd meeting ordinals remain correct even when the first
            # showed meeting happened before the comparison window.
            relevant_appointments = client.fetch_appointments_for_contacts(
                contacts=contacts,
                end_date=end_date,
            )
            user_calendar_events = client.fetch_calendar_events_for_users(
                user_ids=load_report_user_ids(),
                start_date=previous_period_start,
                end_date=end_date,
            )
            rows = overlay_funnel_counts_from_appointments(rows=rows, appointments=relevant_appointments)
            comparison = build_period_comparison(
                rows=rows,
                current_period_start=current_period_start,
                current_label=f"{current_period_start.isoformat()} to {end_date.isoformat()}",
                previous_label=f"{previous_period_start.isoformat()} to {previous_period_end.isoformat()}",
            )
            user_meeting_comparison = build_user_meeting_comparison(
                appointments=user_calendar_events or relevant_appointments,
                previous_start=previous_period_start,
                current_start=current_period_start,
                current_end=end_date,
                ordinal_appointments=relevant_appointments,
            )
            if ga4_data and ga4_data.get("landing_funnel"):
                comparison["ga4_landing"] = build_ga4_landing_comparison(
                    landing_rows=ga4_data["landing_funnel"]["rows"],
                    current_period_start=current_period_start,
                    current_label=f"{current_period_start.isoformat()} to {end_date.isoformat()}",
                    previous_label=f"{previous_period_start.isoformat()} to {previous_period_end.isoformat()}",
                )
            comparison["ghl_status_comparison"] = build_period_ghl_status_comparison(
                contacts=contacts,
                previous_start=previous_period_start,
                previous_end=previous_period_end,
                current_start=current_period_start,
                current_end=end_date,
            )
            current_meta_data = None
            if meta_config is not None:
                current_meta_data = MetaAdsClient(meta_config).fetch_summary_and_breakdowns(
                    start_date=current_period_start,
                    end_date=end_date,
                )
            comparison["quick_snapshot"] = build_comparison_quick_snapshot(
                current_summary=comparison["current_period"]["summary"],
                meta_summary=(current_meta_data or {}).get("summary"),
            )
            logger.info(
                "Built monthly user meeting comparison from %s calendar events and %s contact appointments in %.2fs",
                len(user_calendar_events),
                len(relevant_appointments),
                time.perf_counter() - monthly_appointments_started_at,
            )
            csv_path = output_dir / "monthly_funnel_report.csv"
            html_path = output_dir / "monthly_funnel_report.html"
            pdf_path = output_dir / "monthly_funnel_report.pdf"
            write_weekly_comparison_csv(csv_path=csv_path, comparison=comparison)
            write_weekly_comparison_html(
                html_path=html_path,
                comparison=comparison,
                start_date=fetch_start_date,
                end_date=end_date,
                title="LionCare havi riport",
                user_meeting_comparison=user_meeting_comparison,
                meta_data=current_meta_data,
            )
            write_comparison_pdf(
                pdf_path=pdf_path,
                comparison=comparison,
                title="LionCare havi riport",
                start_date=fetch_start_date,
                end_date=end_date,
                user_meeting_comparison=user_meeting_comparison,
            )
        elif args.report_type == "period_compare":
            if (
                comparison_current_start is None
                or comparison_previous_start is None
                or comparison_previous_end is None
            ):
                raise ValueError("period_compare requires a computed comparison window")

            comparison = build_period_comparison(
                rows=rows,
                current_period_start=comparison_current_start,
                current_label=f"{comparison_current_start.isoformat()} to {end_date.isoformat()}",
                previous_label=f"{comparison_previous_start.isoformat()} to {comparison_previous_end.isoformat()}",
            )
            period_appointments_started_at = time.perf_counter()
            relevant_appointments = client.fetch_appointments_for_contacts(
                contacts=contacts,
                start_date=comparison_previous_start,
                end_date=end_date,
            )
            user_calendar_events = client.fetch_calendar_events_for_users(
                user_ids=load_report_user_ids(),
                start_date=comparison_previous_start,
                end_date=end_date,
            )
            user_meeting_comparison = build_user_meeting_comparison(
                appointments=user_calendar_events or relevant_appointments,
                previous_start=comparison_previous_start,
                current_start=comparison_current_start,
                current_end=end_date,
                ordinal_appointments=relevant_appointments,
            )
            logger.info(
                "Built period user meeting comparison from %s calendar events and %s contact appointments in %.2fs",
                len(user_calendar_events),
                len(relevant_appointments),
                time.perf_counter() - period_appointments_started_at,
            )
            csv_path = output_dir / "period_funnel_report.csv"
            html_path = output_dir / "period_funnel_report.html"
            pdf_path = output_dir / "period_funnel_report.pdf"
            title = f"LionCare időszak riport {comparison_current_start.isoformat()} - {end_date.isoformat()}"
            write_weekly_comparison_csv(csv_path=csv_path, comparison=comparison)
            write_weekly_comparison_html(
                html_path=html_path,
                comparison=comparison,
                start_date=comparison_previous_start,
                end_date=end_date,
                title=title,
                user_meeting_comparison=user_meeting_comparison,
            )
            write_comparison_pdf(
                pdf_path=pdf_path,
                comparison=comparison,
                title=title,
                start_date=comparison_previous_start,
                end_date=end_date,
                user_meeting_comparison=user_meeting_comparison,
            )
        else:
            csv_path = output_dir / "daily_funnel_report.csv"
            html_path = output_dir / "daily_funnel_report.html"
            pdf_path = None
            write_csv_report(csv_path=csv_path, rows=rows)
            write_html_report(
                html_path=html_path,
                rows=rows,
                summary=summary,
                start_date=start_date,
                end_date=end_date,
                ga4_data=ga4_data,
                landing_lead_cards=landing_lead_cards,
                meta_data=meta_data,
                decision_report=decision_report,
            )
            storage_started_at = time.perf_counter()
            persist_daily_report_history(
                report_date=start_date,
                html_path=html_path,
                csv_path=csv_path,
                output_dir=output_dir,
                summary=summary,
                decision_report=decision_report,
                ga4_data=ga4_data,
                meta_data=meta_data,
            )
            logger.info(
                "Completed daily historical storage step in %.2fs",
                time.perf_counter() - storage_started_at,
            )

        print(f"Wrote CSV report to {csv_path}")
        print(f"Wrote HTML report to {html_path}")
        logger.info("Wrote report files csv=%s html=%s pdf=%s", csv_path, html_path, pdf_path)
        if pdf_path is not None:
            print(f"Wrote PDF report to {pdf_path}")

        if should_send_email(args.report_type, args.send_email):
            email_started_at = time.perf_counter()
            send_email_for_report(
                report_type=args.report_type,
                csv_path=csv_path,
                html_path=html_path,
                pdf_path=pdf_path,
                start_date=start_date,
                end_date=end_date,
            )
            logger.info("Sent report email in %.2fs", time.perf_counter() - email_started_at)
            print("Sent report email")

        logger.info("Completed report run in %.2fs", time.perf_counter() - run_started_at)
    except Exception:
        logger.exception("Report run failed after %.2fs", time.perf_counter() - run_started_at)
        raise


def configure_run_logging(output_dir: Path) -> None:
    log_path = output_dir / "report_run.log"
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)


def should_send_email(report_type: str, cli_flag: bool) -> bool:
    if cli_flag:
        return True
    auto_send = os.getenv("REPORT_AUTO_SEND_TYPES", "").strip().lower()
    enabled_types = {item.strip() for item in auto_send.split(",") if item.strip()}
    return report_type.lower() in enabled_types


def send_email_for_report(
    report_type: str,
    csv_path: Path,
    html_path: Path,
    pdf_path: Optional[Path],
    start_date: date,
    end_date: date,
) -> None:
    config = EmailConfig.from_env()
    subject = build_email_subject(report_type=report_type, start_date=start_date, end_date=end_date)
    html_body = html_path.read_text(encoding="utf-8")
    plain_body = (
        f"Attached is the {report_type} GHL funnel report.\n"
        f"Period: {start_date.isoformat()} to {end_date.isoformat()}\n"
    )
    send_report_email(
        config=config,
        subject=subject,
        plain_body=plain_body,
        html_body=html_body,
        attachments=[path for path in [csv_path, html_path, pdf_path] if path is not None],
    )


def build_email_subject(report_type: str, start_date: date, end_date: date) -> str:
    label_map = {
        "daily": "Daily GHL Funnel Report",
        "weekly_compare": "Weekly GHL Funnel Comparison",
        "monthly_compare": "Monthly GHL Funnel Comparison",
    }
    prefix = label_map.get(report_type, "GHL Funnel Report")
    return f"{prefix}: {start_date.isoformat()} to {end_date.isoformat()}"


if __name__ == "__main__":
    main()
