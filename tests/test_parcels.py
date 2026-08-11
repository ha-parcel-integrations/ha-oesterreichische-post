"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping (the part you
rewrite per carrier) can be tested as plain functions.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.oesterreichische_post.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    ParcelStatus,
)
from custom_components.oesterreichische_post.parcels import (
    apply_delivered_filter,
    build_history,
    check_shipment_shape,
    combine_date_time,
    current_state_key,
    format_dimensions,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    normalize_state_key,
    parse_iso,
    sort_parcels_by_ts,
    to_iso_timestamp,
)

from .payloads import (
    ACTIVE_CODE,
    DELIVERED_CODE,
    active_shipment,
    delivered_shipment,
    event,
    pickup_shipment,
)

# ---------------------------------------------------------------------------
# state-key normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "wire,expected",
    [
        ("readyForPickUp", "READY_FOR_PICK_UP"),
        ("readyForPickUpBox", "READY_FOR_PICK_UP_BOX"),
        ("pendingInformation", "PENDING_INFORMATION"),
        ("allesPost", "ALLES_POST"),
        # Already upper-snake keys survive untouched.
        ("DELIVERED", "DELIVERED"),
    ],
)
def test_normalize_state_key_upper_snakes_camel_case(wire, expected):
    """The same transform Post's own app applies before resolving the enum."""
    assert normalize_state_key(wire) == expected


def test_normalize_state_key_handles_missing():
    assert normalize_state_key(None) is None
    assert normalize_state_key("") is None


# ---------------------------------------------------------------------------
# map_parcel_status / map_event_status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        ("pendingInformation", ParcelStatus.REGISTERED),
        ("parcelStamp", ParcelStatus.REGISTERED),
        ("aviso", ParcelStatus.REGISTERED),
        ("deliveryHandOver", ParcelStatus.IN_TRANSIT),
        ("inDistribution", ParcelStatus.IN_TRANSIT),
        ("customsClearance", ParcelStatus.IN_TRANSIT),
        ("deliveryInCustoms", ParcelStatus.IN_TRANSIT),
        ("inDelivery", ParcelStatus.OUT_FOR_DELIVERY),
        ("readyForPickUp", ParcelStatus.AT_PICKUP_POINT),
        ("readyForPickUpStation", ParcelStatus.AT_PICKUP_POINT),
        ("readyForPickUpPoint", ParcelStatus.AT_PICKUP_POINT),
        ("readyForPickUpBox", ParcelStatus.AT_PICKUP_POINT),
        ("delivered", ParcelStatus.DELIVERED),
        ("deliveryInReturn", ParcelStatus.RETURNING),
        ("deliveryDelayed", ParcelStatus.PROBLEM),
        ("deliveryInteruped", ParcelStatus.UNKNOWN),  # typo'd the typo
        ("deliveryInterupted", ParcelStatus.PROBLEM),  # Post's own spelling
        ("notReachable", ParcelStatus.PROBLEM),
    ],
)
def test_map_parcel_status_known(code, expected):
    assert map_parcel_status(code) == expected


def test_parked_parcel_counts_as_delivered():
    """Post's own ``isDelivered()`` includes a parcel left at the Wunschplatz."""
    assert map_parcel_status("deliveryParked") == ParcelStatus.DELIVERED


def test_map_parcel_status_missing_is_unknown():
    assert map_parcel_status(None) == ParcelStatus.UNKNOWN
    assert map_parcel_status("") == ParcelStatus.UNKNOWN


def test_map_parcel_status_unmapped_is_unknown():
    assert map_parcel_status("teleported") == ParcelStatus.UNKNOWN


def test_carrier_unknown_state_is_not_mapped_silently(caplog):
    """Post's own ``UNKNOWN`` means the vocabulary moved — say so."""
    assert map_parcel_status("unknown") == ParcelStatus.UNKNOWN
    assert "UNKNOWN" in caplog.text
    assert "issues/new" in caplog.text


def test_map_event_status_missing_and_unmapped_are_none():
    """History keeps ``null`` rather than ``unknown`` so consumers can tell
    "no mapping" from "mapped to unknown"."""
    assert map_event_status(None) is None
    assert map_event_status("somethingNew") is None
    assert map_event_status("delivered") == ParcelStatus.DELIVERED


def test_unmapped_status_warns_only_once(caplog):
    assert map_parcel_status("abducted") == ParcelStatus.UNKNOWN
    assert map_parcel_status("abducted") == ParcelStatus.UNKNOWN
    assert caplog.text.count("ABDUCTED") == 1
    assert "issues/new" in caplog.text


@pytest.mark.parametrize("code", ["allesPost", "notified"])
def test_uncertain_status_is_mapped_but_self_reports(caplog, code):
    """Pre-1.0: mapped from the vocabulary, never seen on a real parcel."""
    assert map_parcel_status(code) != ParcelStatus.UNKNOWN
    assert map_parcel_status(code) != ParcelStatus.UNKNOWN
    assert caplog.text.count(normalize_state_key(code)) == 1
    assert "issues/new" in caplog.text


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    # A naive value is assumed UTC so mixed lists still sort.
    assert parse_iso("2026-04-29T13:12:42").tzinfo == timezone.utc
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_to_iso_timestamp_anchors_naive_stamps_to_vienna():
    """Post stamps in Austrian local time; reading that as UTC shifts every
    event by an hour or two."""
    assert to_iso_timestamp("2026-04-29T13:12:42") == "2026-04-29T13:12:42+02:00"
    # Winter, so CET rather than CEST.
    assert to_iso_timestamp("2026-01-15T09:00:00") == "2026-01-15T09:00:00+01:00"


def test_to_iso_timestamp_leaves_an_explicit_offset_alone():
    assert to_iso_timestamp("2026-04-29T13:12:42Z") == "2026-04-29T13:12:42+00:00"


def test_to_iso_timestamp_converts_epoch_milliseconds():
    assert to_iso_timestamp(1784203767167) == "2026-07-16T12:09:27.167000+00:00"
    assert to_iso_timestamp(None) is None
    assert to_iso_timestamp(10**20) is None  # out of range -> None, never raises


def test_to_iso_timestamp_passes_garbage_through():
    """parse_iso guards every consumer, so keep the carrier's own text."""
    assert to_iso_timestamp("not-a-date") == "not-a-date"


def test_format_dimensions_needs_all_three_axes():
    assert format_dimensions(30, 20, 10) == {
        "length": 30,
        "width": 20,
        "height": 10,
        "text": "30 x 20 x 10 cm",
    }
    assert format_dimensions(30, None, 10) is None


# ---------------------------------------------------------------------------
# combine_date_time — Post reports the ETA as separate date and time fields
# ---------------------------------------------------------------------------


def test_combine_date_time_joins_a_bare_date_and_time():
    assert combine_date_time("2026-04-29", "13:00") == "2026-04-29T13:00:00+02:00"


def test_combine_date_time_accepts_a_date_alone():
    assert combine_date_time("2026-04-29", None) == "2026-04-29T00:00:00+02:00"


def test_combine_date_time_ignores_an_unusable_time():
    """A reshaped time field degrades to the date, never loses the ETA."""
    assert combine_date_time("2026-04-29", "half past two") == (
        "2026-04-29T00:00:00+02:00"
    )


def test_unusable_eta_time_self_reports_once(caplog):
    """Otherwise the window silently pins to midnight and nobody ever hears."""
    combine_date_time("2026-04-29", "half past two")
    combine_date_time("2026-04-30", "half past two")
    assert caplog.text.count("expected-delivery time") == 1
    assert "half past two" in caplog.text
    assert "issues/new" in caplog.text


def test_usable_eta_time_stays_silent(caplog):
    combine_date_time("2026-04-29", "13:00")
    assert caplog.text == ""


def test_eta_time_is_not_blamed_when_the_date_already_carries_one(caplog):
    """The time field was never used, so it cannot be what is malformed."""
    assert combine_date_time("not a timestamp at all", "13:00") is None
    assert caplog.text == ""


def test_combine_date_time_does_not_double_up_a_full_timestamp():
    assert combine_date_time("2026-04-29T13:00:00", "15:00") == (
        "2026-04-29T13:00:00+02:00"
    )


def test_combine_date_time_handles_missing_and_garbage():
    assert combine_date_time(None, "13:00") is None
    assert combine_date_time("", None) is None
    assert combine_date_time("sometime next week", None) is None


# ---------------------------------------------------------------------------
# current_state_key
# ---------------------------------------------------------------------------


def test_current_state_key_takes_the_newest_event():
    """Post returns events newest-first, and has no machine status of its own."""
    assert current_state_key(delivered_shipment()) == "delivered"


def test_current_state_key_synthesises_pending_information():
    """What Post's own app does for a shipment with no events at all."""
    assert current_state_key({"activeIdentityCode": ACTIVE_CODE}) == (
        "PENDING_INFORMATION"
    )


def test_current_state_key_skips_events_without_a_key():
    shipment = delivered_shipment()
    shipment["sendungsEvents"] = [
        {"timestamp": "2026-04-29T13:12:42"},
        *shipment["sendungsEvents"],
    ]
    assert current_state_key(shipment) == "delivered"


def test_current_state_key_is_none_without_an_identity_code():
    assert current_state_key({}) is None


# ---------------------------------------------------------------------------
# build_history
# ---------------------------------------------------------------------------


def test_build_history_reverses_post_newest_first_order():
    history = build_history(delivered_shipment()["sendungsEvents"])
    assert len(history) == 4
    assert history[0]["raw_status"] == "Shipment announced"
    assert history[0]["status"] == ParcelStatus.REGISTERED
    assert history[-1]["status"] == ParcelStatus.DELIVERED


def test_build_history_caps_to_max_events():
    events = [
        event("inDistribution", f"2026-04-{day:02d}T10:00:00", "moved")
        for day in range(1, 26)
    ]
    assert len(build_history(events, max_events=20)) == 20


def test_build_history_handles_missing_and_malformed():
    assert build_history(None) == []
    assert build_history([{"trackingStateKey": "delivered"}]) == []  # no timestamp
    assert build_history(["not-a-dict"]) == []


def test_build_history_keeps_unparseable_timestamp_last():
    history = build_history(
        [
            event("inDistribution", "not-a-date", "odd"),
            event("pendingInformation", "2026-04-24T10:00:00", "fine"),
        ]
    )
    assert [entry["raw_status"] for entry in history] == ["fine", "odd"]


def test_build_history_falls_back_through_the_text_fields():
    """textEn first, then the German text, then the state key itself."""
    bare = {"trackingStateKey": "inDelivery", "timestamp": "2026-04-24T10:00:00"}
    assert build_history([bare])[0]["raw_status"] == "IN_DELIVERY"

    german = {**bare, "text": "In Zustellung"}
    assert build_history([german])[0]["raw_status"] == "In Zustellung"

    both = {**german, "textEn": "Out for delivery"}
    assert build_history([both])[0]["raw_status"] == "Out for delivery"


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_shipment())) == CANONICAL_KEYS


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(delivered_shipment())
    assert parcel["carrier"] == "Österreichische Post"
    assert parcel["barcode"] == DELIVERED_CODE
    assert parcel["sender"] == "Example Shop"
    # The public surface has no recipient name at all.
    assert parcel["receiver"] is None
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "Delivered"
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-04-29T13:12:42+02:00"
    # A delivered parcel drops its ETA — the window is meaningless once it has
    # arrived.
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["url"] == (
        f"https://www.post.at/s/sendungsdetails?snr={DELIVERED_CODE}"
    )
    assert parcel["weight"] == 1.25
    assert parcel["dimensions"]["text"] == "30 x 20 x 10 cm"
    assert parcel["history"] is None  # opt-in, default off


def test_normalize_falls_back_to_the_original_identity_code():
    shipment = delivered_shipment()
    shipment["activeIdentityCode"] = None
    assert normalize_parcel(shipment)["barcode"] == DELIVERED_CODE


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(delivered_shipment(), include_history=True)
    assert len(parcel["history"]) == 4
    assert parcel["history"][0]["status"] == ParcelStatus.REGISTERED


def test_normalize_active_parcel_has_window():
    parcel = normalize_parcel(active_shipment())
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["delivered"] is False
    assert parcel["planned_from"] == "2026-04-29T13:00:00+02:00"
    assert parcel["planned_to"] == "2026-04-29T15:00:00+02:00"


def test_normalize_falls_back_to_the_single_date_estimate():
    """No window: a point estimate never fills planned_to."""
    shipment = active_shipment()
    shipment["estimatedDelivery"] = None
    shipment["estimatedDeliveryDate"] = "2026-04-29"
    parcel = normalize_parcel(shipment)
    assert parcel["planned_from"] == "2026-04-29T00:00:00+02:00"
    assert parcel["planned_to"] is None


def test_normalize_collapses_point_estimate_to_no_window_end():
    shipment = active_shipment()
    shipment["estimatedDelivery"]["endTime"] = shipment["estimatedDelivery"][
        "startTime"
    ]
    parcel = normalize_parcel(shipment)
    assert parcel["planned_from"] == "2026-04-29T13:00:00+02:00"
    assert parcel["planned_to"] is None


def test_normalize_pickup_parcel():
    parcel = normalize_parcel(pickup_shipment())
    assert parcel["status"] == ParcelStatus.AT_PICKUP_POINT
    assert parcel["pickup"] is True
    # Phase 1 passes the branch key through rather than resolving its name.
    assert parcel["pickup_point"] == "1010-04"


def test_normalize_pending_placeholder():
    """A tracked-but-not-yet-scanned code still yields a full parcel dict."""
    parcel = normalize_parcel({"activeIdentityCode": ACTIVE_CODE})
    # Post synthesises "pending information" for a number nothing has scanned.
    assert parcel["status"] == ParcelStatus.REGISTERED
    assert parcel["delivered"] is False
    assert parcel["raw_status"] == "PENDING_INFORMATION"
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None
    assert parcel["history"] is None


def test_normalize_blank_fields_become_none():
    shipment = active_shipment()
    shipment["shipper"] = {"name": ""}
    shipment["branchkey"] = ""
    parcel = normalize_parcel(shipment)
    assert parcel["sender"] is None
    assert parcel["pickup_point"] is None


def test_normalize_survives_a_missing_shipper_block():
    shipment = active_shipment()
    shipment["shipper"] = None
    assert normalize_parcel(shipment)["sender"] is None


def test_normalize_keeps_raw_payload():
    shipment = active_shipment()
    assert normalize_parcel(shipment)["raw"] is shipment


def test_normalize_falls_back_to_the_state_key_without_display_text():
    shipment = active_shipment()
    shipment["status"] = None
    assert normalize_parcel(shipment)["raw_status"] == "IN_DELIVERY"


def test_delivered_at_uses_the_newest_delivering_event():
    """A parked-then-collected parcel must not report the older moment."""
    shipment = delivered_shipment()
    shipment["sendungsEvents"] = [
        event("delivered", "2026-04-30T09:00:00", "Collected"),
        event("deliveryParked", "2026-04-29T13:12:42", "Left at agreed place"),
        *shipment["sendungsEvents"][1:],
    ]
    assert normalize_parcel(shipment)["delivered_at"] == "2026-04-30T09:00:00+02:00"


def test_delivered_at_is_none_when_no_event_says_so():
    """Defensive: a delivered status without a matching event still normalises."""
    shipment = delivered_shipment()
    shipment["sendungsEvents"] = ["not-a-dict"]
    shipment["status"] = "Delivered"
    parcel = normalize_parcel(shipment)
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None


# ---------------------------------------------------------------------------
# pre-1.0 self-reporting
# ---------------------------------------------------------------------------


def test_missing_mapped_field_warns_once(caplog):
    shipment = active_shipment()
    del shipment["shipper"]
    check_shipment_shape(shipment)
    check_shipment_shape(shipment)
    assert caplog.text.count("'shipper'") == 1
    assert "issues/new" in caplog.text


def test_present_but_null_field_stays_silent(caplog):
    """A parcel with no ETA yet is normal — only a *missing* key is a signal."""
    shipment = active_shipment()
    shipment["shipper"] = None
    check_shipment_shape(shipment)
    assert "shipper" not in caplog.text


def test_assumed_units_self_report_once(caplog):
    check_shipment_shape(active_shipment())
    check_shipment_shape(active_shipment())
    assert caplog.text.count("kilograms") == 1
    assert caplog.text.count("centimetres") == 1


def test_pending_placeholder_does_not_warn(caplog):
    """An unscanned parcel is legitimately near-empty."""
    check_shipment_shape({"activeIdentityCode": ACTIVE_CODE})
    assert caplog.text == ""


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES


def test_capabilities_are_the_full_set():
    """Post attempts every optional contract field — nothing is hard-coded None."""
    assert CAPABILITIES == KNOWN_CAPABILITIES
