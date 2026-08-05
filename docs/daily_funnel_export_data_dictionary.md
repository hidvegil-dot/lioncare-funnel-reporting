# LionCare Daily Funnel Export Data Dictionary

## Source Audit

Primary source systems:

- Meta Marketing API: ad-level performance metrics by `metric_date + account_id + ad_id`.
- GHL Contacts: contact identity key, lead creation timestamp/date, source/funnel labels, landing URL, attribution fields where available.
- GHL Appointments: appointment IDs, creation timestamps, start timestamps, and appointment status.
- GHL Opportunities: opportunity ID, status, created/updated timestamps when available in the daily lead source.

Historical daily CSV files may be either:

- raw-ish lead-level exports with `lead_id`, `contact_id`, `ad_id`, and cohort fields;
- aggregate daily reports with one row per date and no raw lead/ad records.

Aggregate daily reports are not sufficient for corrected ad or lead cohort exports and must be marked `insufficient_source`.

## `daily_ad_performance_YYYY-MM-DD.csv`

Grain: one row per `metric_date + platform + account_id + ad_id`.

Fields:

- `metric_date`: Meta API `date_start`.
- `platform`: constant `Meta` for Meta Marketing API rows.
- `account_id`: Meta ad account ID, string.
- `account_timezone`: configured/reporting timezone, currently `Europe/Budapest`.
- `currency`: reporting currency, currently `HUF`.
- `campaign_id`, `adset_id`, `ad_id`: Meta IDs, string, never numeric.
- `campaign_name`, `adset_name`, `ad_name`: Meta names.
- `spend_huf`, `impressions`, `reach`, `clicks`, `link_clicks`, `landing_page_views`, `registration_leads`: Meta ad-level metrics.
- `source_extracted_at`: timestamp when source snapshot was extracted.
- `exported_at`: timestamp when corrected CSV was written.

## `lead_cohort_YYYY-MM-DD.csv`

Grain: one row per lead. In the current GHL model, `lead_id` is the GHL `contact_id`.

Fields:

- `lead_id`, `contact_id`: GHL contact ID.
- `lead_created_at`: GHL contact creation timestamp when available; date only when only date is present.
- `lead_created_precision`: `datetime` or `date`.
- `source_system`: `GHL`.
- `funnel_name`: GHL source/funnel label. `Lioncare KATA` is not treated as proof of Meta attribution.
- `landing_page_url`: GHL landing URL if available.
- `utm_*`, `fbclid`, `fbc`, `fbp`: attribution parameters if present in source.
- `attribution_status`: `attributed` only when a Meta ad ID is present; otherwise `unattributed`.
- `attribution_method`: evidence used, for example `meta_ad_id`.
- campaign/adset/ad fields: populated only for proven Meta attribution.
- booking/show/no-show/cancel/contract fields: event timestamps/dates from GHL source data only.
- `*_precision`: `datetime` or `date`; no artificial midnight timestamps are allowed.
- opportunity fields: GHL opportunity values, not substitutes for lead creation or contract time.
- `current_status`: status from GHL/contact cohort fields.
- `data_as_of`: timestamp of source state used for the cohort export.
- `exported_at`: timestamp when corrected CSV was written.

## `appointment_events_YYYY-MM-DD.csv`

Grain: one row per appointment/status event.

Fields come from GHL appointment records. Event type is derived from appointment status:

- `booking_created`
- `showed`
- `no_show`
- `cancelled`

Reschedules can only be identified when GHL exposes prior appointment state or separate appointment records.

## Validation Rules

Hard failures:

- damaged Meta ID, scientific notation, rounded or decimal ID;
- duplicate primary key in an output;
- fake midnight timestamp generated from date-only source;
- negative spend or negative count metric.

QA warnings:

- missing `reach`;
- partial or aggregate-only historical source;
- missing event timestamp for a status;
- Meta and CRM lead count differences.
