# Examples

Ready-to-paste Home Assistant snippets for the Österreichische Post integration.

| Folder | Contents |
|---|---|
| [`automations/`](automations/) | YAML automations — copy them into your `automations.yaml` or paste them into the Automation editor in **raw editor** mode. |
| [`dashboards/`](dashboards/) | Lovelace snippets, including [`add_parcel_card.yaml`](dashboards/add_parcel_card.yaml) — track a new parcel straight from a dashboard via the `oesterreichische_post.track_parcel` service. |

All examples assume a single Österreichische Post hub. Adjust entity IDs to match yours.

**Feeding Österreichische Post from e-mail:** Österreichische Post is code-based — every parcel must be registered by its tracking code before it can be tracked. [`automations/track_parcels_from_email.yaml`](automations/track_parcels_from_email.yaml) extracts tracking codes from incoming shipping mails (core IMAP integration + regex, with an optional AI fallback) and registers them automatically; setup guide and pitfalls in [`automations/track_parcels_from_email.md`](automations/track_parcels_from_email.md).

## Services

| Service | Description |
|---|---|
| `oesterreichische_post.track_parcel` | Start tracking a parcel (`tracking_code`). |
| `oesterreichische_post.untrack_parcel` | Stop tracking a parcel (`tracking_code`). |

## Events used in the examples

The coordinator fires these on the HA event bus:

| Event | When | Payload |
|---|---|---|
| `oesterreichische_post_parcel_registered` | A new parcel appears in the active list | The full normalised parcel dict |
| `oesterreichische_post_parcel_status_changed` | A parcel's canonical status changes | Same, plus `old_status` / `new_status` |
| `oesterreichische_post_parcel_delivered` | A parcel reaches the delivered status | Same, plus `old_status` / `new_status` (fires *instead of* `status_changed` on that final hop) |
| `oesterreichische_post_parcel_delivery_time_changed` | A parcel's expected delivery time changes | Same, plus `old_planned_from` / `new_planned_from` / `old_planned_to` / `new_planned_to` |

Events are suppressed on the first refresh after start-up.
