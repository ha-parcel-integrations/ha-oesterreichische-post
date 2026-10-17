# Working in this repository

Home Assistant custom integration for **Österreichische Post** parcel tracking.
Distributed via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
No DTO layer.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — exact key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| ship anything while below 1.0.0 (unconfirmed data) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client, sync requests) | *Deliberate skill divergences* — likely intentional, don't re-flag |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Structure, options flow, dynamic polling and module layout are suite-wide**
and identical in every carrier — the authoritative spec is
[`ha-carrier-template/scaffold/CLAUDE.md`](https://github.com/ha-parcel-integrations/ha-carrier-template/blob/main/scaffold/CLAUDE.md).
This repo follows it exactly.

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).

**API mechanics live in `carrier-research/oesterreichische-post/api/` (private
research repo)** — the keyless GraphQL endpoint, the query, the three-class
error model, the 21-value `TrackingState` vocabulary and the payload mapping, plus
the account-based surface behind Azure AD B2C. Do not duplicate them here.

## Carrier-specific decisions (integration only)

- **Keyless GraphQL, one POST per tracked number.** Nothing is user-supplied
  except the number itself, so there is **no reauth path**: an endpoint that
  stops answering surfaces as an API error, not a credential prompt.
- **The status comes from the newest event, not from the shipment.** Post's
  shipment-level `status` is display text, so it becomes `raw_status` while the
  canonical status is mapped from the newest `trackingStateKey`.
  **`sendungsEvents` arrives oldest-first** — the opposite of what this
  integration assumed until two real parcels were captured on 2026-10-17, which
  made every parcel report the status of its *first* scan (a delivered parcel
  read as `registered`). `_newest_event` now picks by timestamp rather than by
  position, for both the status and `delivered_at`, and falls back to the last
  qualifying event in wire order when a stamp will not parse.
- **Weight and dimensions are real, the units are still assumed.** Both
  parcels carried them (0.45 kg / 35×31×7 and 0.1 kg / 13×13×5 on the
  assumed units), so the fields exist — but nothing in the payload names a
  unit, and the `text` string is built by this integration, not by Post. The
  unit warnings stay until someone weighs a parcel.
- **There is no delivery time window.** `estimatedDelivery` was `null` on both
  parcels and `estimatedDeliveryDate` is display text with no year
  (`"Tuesday, 11/08"`, or `"No estimated delivery date available"`), which is
  why `planned_from`/`planned_to` stay `None` rather than being parsed out of
  prose.
- **A number Post does not know is not an error.** It reads as
  `registered` (Post's own synthesised `PENDING_INFORMATION`), because a printed
  label nobody has scanned is the normal state of a new parcel. Only the
  HTTP 400 `INVALID_ARGUMENT_IDENTITY_CODE` means "malformed", and it is the one
  thing the config flow and `track_parcel` reject — via
  `OesterreichischePostInvalidCodeError`, a subclass so the coordinator's
  existing except clause still catches it.
- **Post's 400 is the only format authority — there is no client-side shape
  check at all.** `valid_tracking_code` just requires a non-empty string;
  real tracking-number formats vary too much to guess a bound worth gating
  on. `async_verify_tracking_code` still calls Post itself and rejects only
  its dedicated `INVALID_ARGUMENT_IDENTITY_CODE` response. Unreachable means
  *accept* — an outage must not block adding a parcel.
- **Naive timestamps are anchored to `Europe/Vienna`, not UTC.** Post stamps in
  local time and Austria is one zone; reading them as UTC would shift every
  event and ETA by an hour or two. `_VIENNA` is resolved at import, never in the
  event loop.
- **`receiver` is always `None`** — the public surface has no recipient name.
  `deliveryAddressText` is free text, not a name, and stays in `raw`.
- **`pickup_point` passes Post's raw `branchkey` through.** Resolving it to a
  branch name needs a second keyless call to `bff/public/branches`; deliberately
  not made in phase 1.
- **`DELIVERY_PARKED` counts as delivered** and `DELIVERY_INTERUPTED` keeps
  Post's own typo — both come from the app's own logic, not from inference.
- **Pre-1.0 self-reporting** (`check_shipment_shape` + `_UNCERTAIN_STATUSES`):
  two real Austrian parcels have been captured but neither settled the units or
  the remaining vocabulary, so a populated shipment missing a mapped field (`status`/`estimatedDelivery`/
  `shipper`/`weight`/`dimensions`), the first sighting of a `weight` or
  `dimensions` value (units **assumed** kg/cm), and the first sighting of
  `ALLES_POST` or `NOTIFIED` each log a one-shot WARNING with the issue link —
  **keys only, no values** (`shipper` and the address fields are PII).
  Presence is by key, so a present-but-null field stays silent. The pending
  placeholder never warns. Remove all of it once a live parcel settles the
  questions.
- **Account-based auto-import exists but is parked.** Post has a Bearer-token
  surface that returns received *and* sent parcels in one call — the
  DPD/PostNL shape — behind Azure AD B2C. It is not built because a B2C login flow
  cannot be written blind, and users on ID Austria / Apple / Google / MFA could
  not be supported anyway. Nothing in the code path above blocks adding it.

## Running tests

```
python -m pytest tests/ --cov=custom_components.oesterreichische_post
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file in the same commit;
the API reference lives in the private `carrier-research/oesterreichische-post/api/`,
not in this repo.

`tests/conftest.py` also clears `parcels.py`'s one-shot warning sets between
tests — they are module-level by design, which would otherwise make "does this
warn?" depend on test order.
