# LionCare Daily Funnel Source Mapping

## Meta Marketing API

Fields sourced directly from Meta ad-level insights:

- `metric_date`: Meta `date_start`
- `platform`: fixed value `Meta` for Meta rows
- `account_id`: Meta ad account ID from config/API metadata
- `campaign_id`, `campaign_name`
- `adset_id`, `adset_name`
- `ad_id`, `ad_name`
- `spend_huf`
- `impressions`
- `reach`
- `clicks`
- `link_clicks`
- `landing_page_views`
- `registration_leads`

The ad export grain is `metric_date + platform + account_id + ad_id`.

## GHL Contacts

Fields sourced from GHL contact records or the daily lead-level export derived from GHL:

- `contact_id`
- `lead_created_at`
- `source_system`
- `funnel_name`
- `landing_page_url`
- UTM and browser attribution fields if present
- `current_status`

Current legacy files do not contain a separate form submission ID. In those files, `lead_id` is generated as a documented deterministic technical key when no opportunity/form/lead event ID is available, and `lead_id_source`/`lead_id_method` show that limitation.

## GHL Appointments

Fields sourced from GHL appointment records when available:

- `appointment_id`
- `event_created_at`
- `event_created_precision`
- `appointment_start_at`
- `appointment_start_precision`
- `new_status`

For corrected backfills, appointment records are requested from GHL per affected contact when credentials are available. If GHL does not expose a required appointment/source record, the day is marked `insufficient_source`; appointment events are not synthesized from lead status.

## GHL Opportunities

Fields sourced from GHL opportunity data when available:

- `opportunity_id`
- `opportunity_created_at`
- `opportunity_updated_at`
- `opportunity_status`

Opportunity timestamps must not be used as substitutes for lead creation or contract timestamps.

## Derived Fields

Derived or normalized fields:

- `lead_created_precision`: `datetime` if the source has an actual timestamp, `date` if only a date exists.
- `normalized_channel`: `paid_social` only when Meta ad attribution is proven.
- `matched_campaign_name`: campaign name populated by matching logic; raw `utm_campaign` remains blank unless the source contained a real UTM value.
- `attribution_status`: `attributed`, `partial`, `uncertain`, or `unattributed`.
- `attribution_method`, `attribution_evidence_type`, `attribution_evidence_value`, `attribution_confidence`: auditable evidence used for attribution. Campaign name or registration date alone is not proof.
- QA metrics such as duplicate counts, fake midnight timestamps, and attribution coverage.

No missing campaign, ad set, ad, or timestamp value may be invented.
