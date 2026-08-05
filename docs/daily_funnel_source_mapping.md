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

Current legacy files do not contain a separate form submission ID. In those files, `lead_id` falls back to the available lead/contact key and must not be interpreted as a separate Meta lead form submission ID.

## GHL Appointments

Fields sourced from GHL appointment records when available:

- `appointment_id`
- `event_created_at`
- `appointment_start_at`
- `new_status`

Legacy CSV backfill does not contain raw appointment records, so `appointment_events` can only be complete for future API-based runs.

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
- `attribution_status`: `attributed`, `partial`, `uncertain`, or `unattributed`.
- `attribution_method`: evidence used for attribution, for example `meta_ad_id`.
- QA metrics such as duplicate counts, fake midnight timestamps, and attribution coverage.

No missing campaign, ad set, ad, or timestamp value may be invented.
