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
# Rate limits: none observed. The suite's 30-minute default cadence keeps the
# load gentle regardless, so the interval stays user-configurable.
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

# Refresh interval (minutes) controls how often the coordinator polls the
# carrier. Default 30 min keeps the load on a consumer endpoint gentle; the
# minimum is 15 min for the same reason.
#
# Deliberate divergence from the HA Core rule that polling intervals are not
# user-configurable: that rule targets core integrations, and in a HACS parcel
# tracker a tunable cadence is a wanted feature. Generate with
# ``--interval fixed`` instead when the carrier throttles or soft-bans unusual
# traffic — that drops the option entirely and hard-codes the cadence, so users
# cannot dial it down to something that gets them blocked.
CONF_REFRESH_INTERVAL = "refresh_interval"
REFRESH_INTERVAL_OPTIONS = (15, 30, 60, 120, 240)
DEFAULT_REFRESH_INTERVAL = 30

# Per-parcel status history is opt-in and off by default, identical across the
# suite. Keep it off by default even when — as here — the timeline arrives in
# the same response and costs no extra request: it is a large attribute, and on
# carriers that need a second call per parcel the cost is real.
CONF_INCLUDE_HISTORY = "include_history"
DEFAULT_INCLUDE_HISTORY = False

# Cap each parcel's history to the most recent N events so the attribute stays
# well under HA's ~16 KB state-attribute limit.
HISTORY_MAX_EVENTS = 20
