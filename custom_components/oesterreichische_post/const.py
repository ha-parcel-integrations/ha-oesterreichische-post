"""Constants for the Österreichische Post parcel tracker integration."""
from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "oesterreichische_post"


class ParcelStatus(StrEnum):
    """Carrier-agnostic parcel status.

    **Do not extend or rename these members.** Every integration in the parcel
    suite publishes exactly this vocabulary on the ``status`` field of each
    normalised parcel, so cross-carrier automations and the aggregator can
    target ``status: out_for_delivery`` regardless of carrier. Listed in
    roughly the order a parcel moves through.
    """

    REGISTERED = "registered"               # Sender announced the parcel; not handed over yet
    IN_TRANSIT = "in_transit"               # In the carrier's network
    OUT_FOR_DELIVERY = "out_for_delivery"   # On a delivery vehicle today
    AT_PICKUP_POINT = "at_pickup_point"     # Ready to collect at a pickup location
    DELIVERED = "delivered"                 # Handed over
    RETURNING = "returning"                 # Failed delivery, going back to sender
    PROBLEM = "problem"                     # Carrier reports an exception/issue
    UNKNOWN = "unknown"                     # Raw status we have not mapped yet


PLATFORMS = [Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR]

# Every optional key the parcel contract defines. CAPABILITIES below must be a
# subset of this — it exists so a typo in CAPABILITIES fails a test instead of
# silently dropping this carrier off a table on the docs site.
KNOWN_CAPABILITIES = frozenset(
    {"weight", "dimensions", "delivery_window", "pickup_point", "url", "history"}
)

# Which optional contract fields this carrier's API actually populates — feeds
# the comparison table on the docs site. Keep in lockstep with
# normalize_parcel() in parcels.py: everything not listed here comes back as a
# literal None there. Österreichische Post is one of the suite's most
# complete carriers — it attempts every optional contract field, weight and
# dimensions included (assumed kg/cm, unverified against a real parcel).
CAPABILITIES = frozenset(
    {"weight", "dimensions", "delivery_window", "pickup_point", "url", "history"}
)

# The public GraphQL endpoint the integration polls. Keyless: no API key, no
# Origin/Referer check, one POST per tracking code. Two hosts serve the same
# schema; this one speaks the plain GraphQL envelope, while the app's
# ``bff/public/shipments`` wraps every reply in an extra
# ``{responseHeaders, statusCode, data, errors}`` layer for nothing.
#
# Content type is ``application/json`` on the way in and out, but read replies
# with ``content_type=None`` anyway — the error responses have been seen served
# as text.
#
# Rate limits: none observed. The dynamic polling cadence below keeps the
# load gentle regardless.
TRACKING_API_URL = "https://api.post.at/sendungen/sv/graphqlPublic"

# The app's own deep link into the consumer tracking page.
TRACKING_URL = "https://www.post.at/s/sendungsdetails?snr={tracking_code}"

# The single public query. ``einzelsendung`` is the only field on ``Query``.
#
# This is the app's own ``ShipmentPublic`` operation, widened with the fields
# schema introspection shows exist but the app does not ask for
# (``estimatedDelivery``, ``shipper``, ``deliveryType``, ``branchkey``).
# Introspection is enabled on this endpoint, so dump the schema rather than
# field-error-walking if something else is needed later.
#
# Send it as ``{"query": ..., "variables": {"id": "<code>"}}`` — inline
# arguments are refused, only the variable form is accepted.
TRACKING_QUERY = """
query ShipmentPublic($id: String!) {
  einzelsendung(sendungsnummer: $id) {
    activeIdentityCode originalIdentityCode status produktkategorie productType
    weight branchkey deliveryType deliveryAddressText estimatedDeliveryDate
    estimatedDeliveryDateText
    estimatedDelivery { startDate endDate startTime endTime }
    dimensions { height length width }
    shipper { name postalCode city country }
    sendungsEvents {
      trackingStateKey trackingState trackingDesc text textEn timestamp
      eventcountry eventpostalcode eventPlaceName
      stateInfo { titleText descText }
    }
    customsInformation { customsDocumentAvailable userDocumentNeeded }
  }
}
"""

# The endpoint's whole error model is three response classes:
#
#   * HTTP 200 + ``data.einzelsendung: null`` — unknown or not-yet-scanned code.
#     A normal state, never an error.
#   * HTTP 400 + this extension code — the number itself is malformed. The API
#     is the authority on the format, which is why the local regex in
#     ``config_flow`` stays permissive.
#   * HTTP 200 + a top-level ``errors[]`` — schema drift or a server fault.
#     Raise, so the coordinator keeps serving its cached payload.
ERROR_INVALID_IDENTITY_CODE = "INVALID_ARGUMENT_IDENTITY_CODE"

# Tracked parcels live in the config entry options as a list of
# ``{tracking_code}`` dicts — this carrier has no account or parcel feed, so the
# user enters the codes themselves. Kept as dicts so future per-parcel fields
# slot in without an options migration.
CONF_PARCELS = "parcels"
CONF_TRACKING_CODE = "tracking_code"

# Delivered-parcels retention: keep delivered parcels visible for the last N
# days, or keep only the N most recent — identical across the suite.
CONF_DELIVERED_FILTER_TYPE = "delivered_filter_type"
CONF_DELIVERED_FILTER_AMOUNT = "delivered_filter_amount"
DEFAULT_DELIVERED_FILTER_TYPE = "days"
DEFAULT_DELIVERED_FILTER_AMOUNT = 7

# Dynamic, status-driven polling — unconditional, no user-facing interval
# option. See carrier-research/dynamic-polling.md for the full algorithm and
# the reasoning behind it.
#
# Quiet window: no polling between these local hours except the two anchors
# below, for overnight / end-of-day catch-up.
QUIET_WINDOW_START_HOUR = 0
QUIET_WINDOW_END_HOUR = 6

# Cadence while polling is active (minutes). Hot = at least one tracked,
# not-yet-delivered parcel is out_for_delivery within HOT_LOOKAHEAD_HOURS of
# its planned_from (or has no planned_from at all); mid = anything else still
# in flight. This is a barcode-based coordinator (Section 2.1): when every
# tracked parcel is delivered, or nothing is tracked, polling stops entirely
# instead of falling to the mid tier — see coordinator.py's
# ``_hottest_tier_minutes``.
HOT_INTERVAL_MINUTES = 15
MID_INTERVAL_MINUTES = 45
HOT_LOOKAHEAD_HOURS = 1

# Small, stable per-install offset added to every computed interval so
# different installs don't all hit an anchor or tier boundary at the same
# second. Deterministic (hash of the config entry id), not random.
STAGGER_MINUTES = 7

# Per-parcel status history is opt-in and off by default, identical across the
# suite. Keep it off by default even when — as here — the timeline arrives in
# the same response and costs no extra request: it is a large attribute, and on
# carriers that need a second call per parcel the cost is real.
CONF_INCLUDE_HISTORY = "include_history"
DEFAULT_INCLUDE_HISTORY = False

# Cap each parcel's history to the most recent N events so the attribute stays
# well under HA's ~16 KB state-attribute limit.
HISTORY_MAX_EVENTS = 20
