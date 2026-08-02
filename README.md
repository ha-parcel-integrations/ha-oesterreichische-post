# Österreichische Post Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-oesterreichische-post.svg)](https://github.com/ha-parcel-integrations/ha-oesterreichische-post/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your [Österreichische Post](https://www.post.at) (Austrian Post) parcels. No account and no API key are needed — you enter the tracking number yourself, just like on the Post website.

Part of the [ha-parcel-integrations](https://github.com/ha-parcel-integrations) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

> ### ⚠️ Early release — no real Austrian parcel has been tracked yet
>
> The endpoint is live and keyless, its error handling is verified against it,
> and the status vocabulary is complete — it was lifted from Post's own app, so
> nothing about the mapping is guessed. What has **not** been seen from a real
> parcel is a *populated* response, so three things are still open:
>
> - whether the expected delivery window, sender, weight and dimensions are
>   filled in at all on the public endpoint;
> - whether weight is in **kilograms** and dimensions in **centimetres** (both
>   assumed);
> - whether `ALLES_POST` and `NOTIFIED` land in the right canonical status.
>
> Each of those logs a one-shot warning with a ready-made issue link the first
> time a real parcel hits it, and an unmapped status reports **`unknown`**
> rather than a wrong one. If you see one,
> [please report it](https://github.com/ha-parcel-integrations/ha-oesterreichische-post/issues/new?template=unrecognised_status.yml)
> — that is what finishes this integration.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Track any number of Österreichische Post parcels by tracking number — no account needed
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `out_for_delivery` / `delivered` / …), the carrier's own status text, the expected delivery window and a tracking deep-link
- Summary sensors: incoming parcels, next delivery, recently delivered parcels
- Read-only **Deliveries** calendar with the expected delivery windows
- `oesterreichische_post.track_parcel` / `oesterreichische_post.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered, delivery time changed)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2024.7 or newer
- An Österreichische Post parcel and its tracking number (from the shipping
  confirmation email, the parcel label or the missed-delivery card) — no account
  needed. Both Post's own numbers and international UPU codes (`RR123456789AT`)
  work.

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-oesterreichische-post` as an **Integration**.
3. Install **Österreichische Post** and restart Home Assistant.

### Manual

Copy `custom_components/oesterreichische_post` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → Österreichische Post**. There is nothing to fill in: the hub is created immediately (Österreichische Post tracking needs no account).

Then add parcels via the integration's **Configure** dialog, the [`oesterreichische_post.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The tracking number is on your shipping confirmation email, the parcel label or the missed-delivery card. It is checked with Post as you add it, so a typo is caught right away — but a number Post does not know *yet* is accepted, because that is simply a parcel nobody has scanned so far.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |
| Polling | Refresh every | 30 min | How often Österreichische Post is checked. Slower is gentler on their API. |

## Removal

Standard HA removal applies: **Settings → Devices & Services → Österreichische Post → ⋮ → Delete**. Nothing is stored on Österreichische Post's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.osterreichische_post_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.osterreichische_post_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.osterreichische_post_next_delivery` | Earliest expected delivery moment across all active parcels |
| `sensor.osterreichische_post_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.osterreichische_post_last_successful_update` | Diagnostic: when Österreichische Post was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family:

| Status | Meaning |
|---|---|
| `registered` | Announced, label printed, or not yet scanned |
| `in_transit` | In the sorting network, customs included |
| `out_for_delivery` | With the courier today |
| `at_pickup_point` | Waiting for you at a branch, post partner, pickup station or locker |
| `delivered` | Delivered, including left at your agreed *Wunschplatz* |
| `returning` | Going back to the sender |
| `problem` | Post reports a delay, an interruption or that you could not be reached |
| `unknown` | A status we have not mapped yet — please report it |

Post's own human-readable text is always available as `raw_status`.

## Events

The integration fires these on the event bus (also available as device triggers on the Österreichische Post device):

| Event | When |
|---|---|
| `oesterreichische_post_parcel_registered` | A new parcel appears in the active list |
| `oesterreichische_post_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `oesterreichische_post_parcel_delivered` | A parcel is delivered |
| `oesterreichische_post_parcel_delivery_time_changed` | The expected delivery window changes |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `oesterreichische_post.track_parcel` | `tracking_code` | Start tracking a parcel |
| `oesterreichische_post.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.oesterreichische_post: debug
```

## Troubleshooting

- **A parcel sits on `registered` with no details** — Post has not scanned it yet (the endpoint reports the number as empty until the first scan). It fills in automatically once the parcel enters the network.
- **A parcel shows `unknown`** — Post reported a status this integration does not map yet. The log line names it; please report it (below) and it will be mapped.
- **A warning about a status, a field or a unit** — that is this integration asking for the confirmation it still needs (see the early-release note above). Please [open an issue](https://github.com/ha-parcel-integrations/ha-oesterreichische-post/issues/new?template=unrecognised_status.yml) with the logged line.
- **A pickup parcel shows a code instead of a branch name** — `pickup_point` currently passes Post's raw branch key through; resolving it to the branch's name needs a second call and is not made yet.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://github.com/ha-parcel-integrations) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://github.com/ha-parcel-integrations) for the current list of supported carriers.

## Disclaimer

This integration uses the same public, keyless tracking endpoint as the Österreichische Post consumer website and app. It is not affiliated with, endorsed by, or supported by Österreichische Post AG. Be gentle with the polling interval.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
