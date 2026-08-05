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

Grain: one row per lead, form submission, or opportunity-level lead event. `lead_id` must not silently equal `contact_id`.

Fields:

- `lead_id`: true form submission ID, true lead event ID, opportunity ID, or documented deterministic technical key.
- `lead_id_source`: source used to create `lead_id`.
- `lead_id_method`: method used to create `lead_id`; `contact_source_timestamp_hash` means no true lead event ID was available.
- `contact_id`: GHL contact ID.
- `lead_created_at`: GHL contact creation timestamp when available; date only when only date is present.
- `lead_created_precision`: `datetime` or `date`.
- `source_system`: `GHL`.
- `funnel_name`: GHL source/funnel label. `Lioncare KATA` is not treated as proof of Meta attribution.
- `landing_page_url`: GHL landing URL if available.
- `utm_*`, `fbclid`, `fbc`, `fbp`: raw attribution parameters if present in source. Derived campaign names must not be written into raw UTM fields.
- `matched_campaign_name`: campaign name populated by matching logic, separate from raw `utm_campaign`.
- `attribution_status`: `attributed` only with direct auditable evidence; `partial` with partial evidence; `uncertain` with non-auditable legacy matching; otherwise `unattributed`.
- `attribution_method`, `attribution_evidence_type`, `attribution_evidence_value`, `attribution_confidence`: attribution proof and confidence.
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

`event_created_precision` and `appointment_start_precision` are mandatory and may only be `datetime` or `date`. Reschedules can only be identified when GHL exposes prior appointment state or separate appointment records.

## Validation Rules

Hard failures:

- damaged Meta ID, scientific notation, rounded or decimal ID;
- duplicate primary key in an output;
- fake midnight timestamp generated from date-only source;
- negative spend or negative count metric;
- derived campaign name in raw UTM field;
- `attributed` lead without auditable evidence;
- undocumented `lead_id = contact_id`;
- booked/showed/no-show/cancelled lead without appointment source or documented insufficient source.

QA warnings:

- missing `reach`;
- partial or aggregate-only historical source;
- missing event timestamp for a status;
- Meta and CRM lead count differences.
