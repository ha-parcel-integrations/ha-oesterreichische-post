"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

Carrier-specific here: :data:`_STATUS_MAP`, the camelCase → ``UPPER_SNAKE``
state-key normaliser, :func:`normalize_parcel`, the ETA window builder and the
pre-1.0 self-reporting warnings. Everything else — the sort contract, the
delivered filter, the one-shot warning for unmapped statuses — is suite-wide
machinery and should be left alone.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-oesterreichische-post/issues/new"
    "?template=unrecognised_status.yml"
)

# Austria is one time zone, and Post's timestamps have been seen without an
# offset. Resolved at import (never in the event loop) so a naive stamp can be
# anchored to Vienna rather than silently read as UTC — a one-or-two-hour error
# on every ETA and delivery moment.
_VIENNA = ZoneInfo("Europe/Vienna")

# Post sends its state keys in camelCase (``readyForPickUp``); its own app
# upper-snakes them before resolving them against the enum. Same transform here,
# so :data:`_STATUS_MAP` can be written in the enum's own spelling.
_CAMEL_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])")

# Post's ``TrackingState`` vocabulary, complete (21 values), mapped onto the
# canonical enum. Written in the carrier's own spelling — including
# ``DELIVERY_INTERUPTED``, which is Post's own typo and must be kept verbatim.
#
# ``UNKNOWN`` is deliberately *absent*: it is the app's fallback for a key it
# does not recognise, so seeing it on the wire means the vocabulary moved. It
# lands on ``unknown`` plus the one-shot warning like any unmapped value, which
# is exactly the signal we want.
_STATUS_MAP: dict[str, ParcelStatus] = {
    # Announced, label printed, or handed to Post but not yet in the network
    "PENDING_INFORMATION": ParcelStatus.REGISTERED,
    "PARCEL_STAMP": ParcelStatus.REGISTERED,
    "AVISO": ParcelStatus.REGISTERED,
    "ALLES_POST": ParcelStatus.REGISTERED,
    # Moving through the network, customs included
    "DELIVERY_HAND_OVER": ParcelStatus.IN_TRANSIT,
    "IN_DISTRIBUTION": ParcelStatus.IN_TRANSIT,
    "CUSTOMS_CLEARANCE": ParcelStatus.IN_TRANSIT,
    "DELIVERY_IN_CUSTOMS": ParcelStatus.IN_TRANSIT,
    # On the van today
    "IN_DELIVERY": ParcelStatus.OUT_FOR_DELIVERY,
    # Waiting to be collected — branch, post partner, station or locker
    "NOTIFIED": ParcelStatus.AT_PICKUP_POINT,
    "READY_FOR_PICK_UP": ParcelStatus.AT_PICKUP_POINT,
    "READY_FOR_PICK_UP_STATION": ParcelStatus.AT_PICKUP_POINT,
    "READY_FOR_PICK_UP_POINT": ParcelStatus.AT_PICKUP_POINT,
    "READY_FOR_PICK_UP_BOX": ParcelStatus.AT_PICKUP_POINT,
    # Arrived. DELIVERY_PARKED is a parcel left at the agreed Wunschplatz, and
    # Post's own ``isDelivered()`` counts it as delivered — so do we.
    "DELIVERED": ParcelStatus.DELIVERED,
    "DELIVERY_PARKED": ParcelStatus.DELIVERED,
    # Going back to the sender
    "DELIVERY_IN_RETURN": ParcelStatus.RETURNING,
    # Something went wrong; the parcel is still in the network
    "DELIVERY_DELAYED": ParcelStatus.PROBLEM,
    "DELIVERY_INTERUPTED": ParcelStatus.PROBLEM,  # sic — Post's spelling
    "NOT_REACHABLE": ParcelStatus.PROBLEM,
}

# Pre-1.0: two rows above are reasoned from the vocabulary rather than seen on a
# real parcel. ``ALLES_POST`` is Post's own bundling product and ``NOTIFIED``
# could equally mean "collection notice sent" (pickup) or "attempted delivery"
# (problem). Both are mapped, and both self-report the first time a real parcel
# hits them so the guess gets confirmed or corrected. Drop this set once a live
# parcel has shown either.
_UNCERTAIN_STATUSES = frozenset({"ALLES_POST", "NOTIFIED"})

# Status codes we have already warned about, so each unmapped one is logged
# only once per HA session instead of on every poll.
_unmapped_statuses_logged: set[str] = set()
_uncertain_statuses_logged: set[str] = set()


def _warn_unmapped_status(code: str) -> None:
    """Log an unmapped carrier status once, with a copy-paste issue link."""
    if code in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(code)
    _LOGGER.warning(
        "Unrecognised Österreichische Post status — help us map it. Open an issue "
        "and paste this line: %s\n  status=%s → reported as 'unknown'",
        NEW_ISSUE_URL,
        code,
    )


def _warn_uncertain_status(code: str) -> None:
    """Ask once for confirmation of a status we mapped but have never seen."""
    if code in _uncertain_statuses_logged:
        return
    _uncertain_statuses_logged.add(code)
    _LOGGER.warning(
        "Österreichische Post reported the %s status, which we map without ever "
        "having seen it on a real parcel — it is reported as '%s'. Please help "
        "us confirm that is right by opening an issue and pasting this line "
        "along with what the parcel was actually doing: %s",
        code,
        _STATUS_MAP[code].value,
        NEW_ISSUE_URL,
    )


def normalize_state_key(key: str | None) -> str | None:
    """Return a ``trackingStateKey`` in the vocabulary's own spelling.

    Post sends camelCase (``readyForPickUp``) while its ``TrackingState`` enum
    — and therefore :data:`_STATUS_MAP` — is ``UPPER_SNAKE``. This is the same
    transform the app applies before resolving the key.
    """
    if not key:
        return None
    return _CAMEL_BOUNDARY_RE.sub(r"\1_\2", str(key)).upper()


def map_parcel_status(code: str | None) -> ParcelStatus:
    """Map a carrier status code to a canonical :class:`ParcelStatus`.

    ``None`` (a not-yet-scanned parcel) reports ``unknown`` silently; an
    unrecognised code reports ``unknown`` with a one-shot warning.
    """
    normalized = normalize_state_key(code)
    if not normalized:
        return ParcelStatus.UNKNOWN
    mapped = _STATUS_MAP.get(normalized)
    if mapped is not None:
        if normalized in _UNCERTAIN_STATUSES:
            _warn_uncertain_status(normalized)
        return mapped
    _warn_unmapped_status(normalized)
    return ParcelStatus.UNKNOWN


def map_event_status(code: str | None) -> ParcelStatus | None:
    """Map a history entry's status code to a canonical status, or ``None``.

    Post's events carry the *same* ``trackingStateKey`` vocabulary as the parcel
    itself, so one map serves both. Unmapped codes keep ``status: null`` on the
    history entry (rather than ``unknown``, so a consumer can tell "no mapping"
    from "mapped to unknown") and warn once, reusing the parcel-status one-shot
    set.
    """
    normalized = normalize_state_key(code)
    if not normalized:
        return None
    mapped = _STATUS_MAP.get(normalized)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(normalized)
    return None


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for an API timestamp field.

    Numbers are treated as **epoch milliseconds**, the suite-wide default.
    Strings are parsed and, when they carry **no offset**, anchored to
    ``Europe/Vienna`` rather than UTC: Post stamps its events in local time and
    Austria is a single zone, so reading them as UTC would shift every event and
    ETA by an hour or two. Anything unparseable passes through untouched — its
    consumers are guarded by :func:`parse_iso`.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_VIENNA)
    return parsed.isoformat()


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """Return the canonical ``dimensions`` dict, or ``None`` when incomplete.

    Units contract: **centimetres**, with ``text`` pre-formatted as
    ``"L x W x H cm"`` (integer values, lowercase ``x``) so dashboards can show
    a dimension without doing their own formatting. Convert before calling if
    the carrier reports millimetres or inches.
    """
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{int(length)} x {int(width)} x {int(height)} cm",
    }


def build_history(
    events: list | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from the carrier's event list.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. ``raw_status`` is the carrier's own text, or
    its event code when the API has no human-readable text. Sorted oldest →
    newest and capped to the most recent ``max_events``.

    Post returns ``sendungsEvents`` **newest-first** (its app reads
    ``events.first()`` as the current state), so the sort below is not a
    formality — it is what puts the timeline the right way round.

    ``textEn`` is preferred over the German ``text`` for ``raw_status``: it is
    the same event in the user's more likely second language, and Post populates
    both.
    """
    parseable: list[tuple[datetime, dict]] = []
    unparseable: list[dict] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        timestamp = to_iso_timestamp(event.get("timestamp"))
        if not timestamp:
            continue
        state_key = event.get("trackingStateKey")
        entry = {
            "timestamp": timestamp,
            "status": map_event_status(state_key),
            "raw_status": (
                event.get("textEn")
                or event.get("text")
                or event.get("trackingState")
                or normalize_state_key(state_key)
            ),
        }
        parsed = parse_iso(timestamp)
        if parsed is None:
            unparseable.append(entry)
        else:
            parseable.append((parsed, entry))
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable] + unparseable
    return ordered[-max_events:]


# Pre-1.0 self-reporting. The query was built from a live schema dump, so the
# *field names* are certain — but no real Austrian parcel has ever come back
# through it, so which fields are actually populated, and in which unit, is not.
# Both warnings below exist to get that confirmed by the first real user instead
# of by waiting; remove them once a live parcel has settled it. Keys only, never
# values — ``shipper`` and the address fields are PII.
_shape_fields_logged: set[str] = set()
_units_logged: set[str] = set()
_eta_shape_logged: set[str] = set()

# Fields we map that a populated shipment is expected to carry. Presence is
# tested by key, not truthiness, so a present-but-null field (a parcel that
# genuinely has no ETA yet) stays silent; only a *missing* key — the signal that
# the public surface serves a narrower shape than the schema advertises — warns.
_EXPECTED_FIELDS = (
    "status",
    "estimatedDelivery",
    "shipper",
    "weight",
    "dimensions",
)


def _warn_missing_field(field: str) -> None:
    """Log a mapped field absent from a populated shipment, once."""
    if field in _shape_fields_logged:
        return
    _shape_fields_logged.add(field)
    _LOGGER.warning(
        "Österreichische Post shipment is missing the %r field we expected to "
        "map — the public response may be narrower than the schema advertises. "
        "Please help us confirm it by opening an issue and pasting this line "
        "(redacted diagnostics ideal): %s",
        field,
        NEW_ISSUE_URL,
    )


def _warn_assumed_unit(field: str, assumed: str) -> None:
    """Ask once for confirmation of a unit we assumed rather than saw."""
    if field in _units_logged:
        return
    _units_logged.add(field)
    _LOGGER.warning(
        "Österreichische Post reported %s for the first time and we are "
        "publishing it as %s without ever having confirmed the unit. Please "
        "help us check it against the parcel's real %s by opening an issue and "
        "pasting this line: %s",
        field,
        assumed,
        field,
        NEW_ISSUE_URL,
    )


def _warn_unusable_eta_time(value: Any) -> None:
    """Report once that Post's ETA time field did not combine into an instant.

    Logging the value is deliberate and safe here: it is a clock time in a
    delivery window, not an identifier, and its *format* is exactly what has to
    be seen to fix the parser.
    """
    if _eta_shape_logged:
        return
    _eta_shape_logged.add("estimatedDelivery")
    _LOGGER.warning(
        "Österreichische Post reported an expected-delivery time we could not "
        "combine with its date (%r), so the delivery window falls back to the "
        "date alone. Please help us parse it by opening an issue and pasting "
        "this line: %s",
        value,
        NEW_ISSUE_URL,
    )


def check_shipment_shape(raw: dict) -> None:
    """One-shot WARNINGs for the parts of the payload still unconfirmed.

    Skips the coordinator's pending placeholder (a bare identity code with no
    events): an unscanned parcel is legitimately near-empty, and warning on it
    would fire for every user who adds a code before it is scanned.
    """
    if not raw.get("sendungsEvents"):
        return
    for field in _EXPECTED_FIELDS:
        if field not in raw:
            _warn_missing_field(field)
    if raw.get("weight") is not None:
        _warn_assumed_unit("weight", "kilograms")
    if raw.get("dimensions"):
        _warn_assumed_unit("dimensions", "centimetres")


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=tracking_code)


def combine_date_time(date_part: Any, time_part: Any) -> str | None:
    """Join Post's separate ETA date and time into one ISO 8601 instant.

    ``estimatedDelivery`` reports ``startDate``/``startTime`` and
    ``endDate``/``endTime`` as separate fields. The date alone is enough; the
    time is optional and dropped when it does not combine into something
    parseable, so a reshaped time field degrades to a date rather than losing
    the ETA entirely.

    Pre-1.0: that degradation is otherwise **silent** — the user would just see
    a window pinned to midnight — so a time we cannot use self-reports once. The
    exact shape of these two fields has never been seen on a real parcel.
    """
    if not date_part:
        return None
    date_text = str(date_part)
    if time_part:
        # Post's own date field may already carry a time; joining then would
        # produce nonsense, so only join a bare date.
        already_timed = "T" in date_text or " " in date_text
        joined = date_text if already_timed else f"{date_text}T{time_part}"
        combined = to_iso_timestamp(joined)
        if combined and parse_iso(combined) is not None:
            return combined
        if not already_timed:
            _warn_unusable_eta_time(time_part)
    date_only = to_iso_timestamp(date_text)
    return date_only if date_only and parse_iso(date_only) is not None else None


def current_state_key(raw: dict) -> str | None:
    """Return the state key describing where the parcel is *now*.

    Post has no machine-readable status on the shipment itself — its ``status``
    field is display text — so the current state is the newest event's
    ``trackingStateKey``, which is the first one in the list.

    A shipment with no events at all falls back to ``PENDING_INFORMATION``, the
    same value the app synthesises: the number is known (or was just entered by
    the user) but nothing has scanned it yet. ``PARCEL_STAMP`` needs no
    synthesising — a label-only shipment carries it as a real event.
    """
    for event in raw.get("sendungsEvents") or []:
        if isinstance(event, dict) and event.get("trackingStateKey"):
            return str(event["trackingStateKey"])
    if raw.get("activeIdentityCode") or raw.get("originalIdentityCode"):
        return "PENDING_INFORMATION"
    return None


def delivered_timestamp(events: list | None) -> str | None:
    """Return the newest event that means "delivered", as an ISO timestamp.

    The list arrives newest-first, so the first match is the right one.
    """
    for event in events or []:
        if not isinstance(event, dict):
            continue
        if map_event_status(event.get("trackingStateKey")) is ParcelStatus.DELIVERED:
            return to_iso_timestamp(event.get("timestamp"))
    return None


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    The **keys of the returned dict are the contract**: every carrier in the
    suite returns exactly these, in this order. A key Post does not expose is
    ``None``, never omitted.

    Carrier notes:

    * ``status`` comes from the newest event's ``trackingStateKey``; the
      shipment's own ``status`` is display text and becomes ``raw_status``.
    * ``receiver`` is always ``None`` — the public surface has no recipient
      name. ``deliveryAddressText`` is free text, not a name, and stays in
      ``raw``.
    * ``pickup_point`` is Post's raw ``branchkey``. Resolving it to a branch
      name needs a second keyless call and is deliberately not made yet.
    * ``weight`` (kg) and ``dimensions`` (cm) are assumed units — see
      :func:`check_shipment_shape`.
    """
    check_shipment_shape(raw)

    tracking_code = raw.get("activeIdentityCode") or raw.get("originalIdentityCode")
    state_key = current_state_key(raw)
    status = map_parcel_status(state_key)
    delivered = status is ParcelStatus.DELIVERED
    events = raw.get("sendungsEvents") or []

    eta = raw.get("estimatedDelivery") or {}
    planned_from = combine_date_time(eta.get("startDate"), eta.get("startTime"))
    planned_to = combine_date_time(eta.get("endDate"), eta.get("endTime"))
    if not planned_from:
        # No window: fall back to the single-date estimate, which leaves
        # planned_to empty because a point estimate is not a window.
        planned_from = combine_date_time(raw.get("estimatedDeliveryDate"), None)
        planned_to = None
    if planned_from and planned_to and parse_iso(planned_to) == parse_iso(planned_from):
        # Same instant twice is a point estimate, not a window.
        planned_to = None

    shipper = raw.get("shipper") or {}
    dimensions = raw.get("dimensions") or {}

    return {
        "carrier": "Österreichische Post",
        "barcode": tracking_code,
        "sender": shipper.get("name") or None,
        "receiver": None,
        "status": status,
        "raw_status": raw.get("status") or normalize_state_key(state_key),
        "delivered": delivered,
        "delivered_at": delivered_timestamp(events) if delivered else None,
        "planned_from": None if delivered else planned_from,
        "planned_to": None if delivered else planned_to,
        "pickup": status is ParcelStatus.AT_PICKUP_POINT,
        "pickup_point": raw.get("branchkey") or None,
        "url": tracking_url(tracking_code),
        "weight": raw.get("weight"),
        "dimensions": format_dimensions(
            dimensions.get("length"), dimensions.get("width"), dimensions.get("height")
        ),
        "history": build_history(events) if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
