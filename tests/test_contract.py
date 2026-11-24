"""The parcel contract's checks, run over every normalised sample."""

from datetime import datetime

import pytest

from custom_components.oesterreichische_post.const import ParcelStatus
from custom_components.oesterreichische_post.parcels import normalize_parcel, parse_iso

from .payloads import active_shipment, delivered_shipment, event, pickup_shipment

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "contents",
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


def _with_event(key: str, stamp: str = "2026-04-29T14:00:00") -> dict:
    sample = pickup_shipment()
    sample["sendungsEvents"] = [event(key, stamp, key), *sample["sendungsEvents"]]
    return sample


def _collected() -> dict:
    return _with_event("delivered", "2026-04-29T16:00:00")


def _moment_only() -> dict:
    sample = active_shipment()
    sample["estimatedDelivery"] = {
        "startDate": "2026-04-29",
        "startTime": "13:00",
        "endDate": None,
        "endTime": None,
    }
    return sample


def _date_only() -> dict:
    sample = active_shipment()
    sample["estimatedDelivery"] = {
        "startDate": "2026-04-29",
        "startTime": None,
        "endDate": None,
        "endTime": None,
    }
    return sample


def _home_with_branchkey() -> dict:
    sample = active_shipment()
    sample["branchkey"] = "1010-04"
    return sample


SAMPLES = {
    "delivered": delivered_shipment,
    "active": active_shipment,
    "at_pickup_point": pickup_shipment,
    "collected": _collected,
    "returning": lambda: _with_event("deliveryInReturn"),
    "problem": lambda: _with_event("deliveryDelayed"),
    "moment_only": _moment_only,
    "date_only": _date_only,
    "home_with_branchkey": _home_with_branchkey,
    "bare": lambda: {"activeIdentityCode": "EXAMPLE000001"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_the_samples_cover_the_statuses_that_empty_the_window():
    statuses = {normalize_parcel(make())["status"] for make in SAMPLES.values()}
    assert {
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
        ParcelStatus.PROBLEM,
    } <= statuses


def test_a_collected_pickup_parcel_stays_pickup():
    parcel = normalize_parcel(_collected())
    assert parcel["delivered"] is True
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "1010-04"
    assert parcel["delivered_at"] == "2026-04-29T16:00:00+02:00"


def test_a_home_delivery_with_a_branchkey_is_not_pickup():
    parcel = normalize_parcel(_home_with_branchkey())
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None


def test_a_moment_ends_at_the_end_of_that_day():
    parcel = normalize_parcel(_moment_only())
    end = datetime.fromisoformat(parcel["planned_to"])
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert end.date() == datetime.fromisoformat(parcel["planned_from"]).date()


def test_a_date_covers_the_whole_day():
    parcel = normalize_parcel(_date_only())
    start = datetime.fromisoformat(parcel["planned_from"])
    end = datetime.fromisoformat(parcel["planned_to"])
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert start.date() == end.date()
    assert (
        start.utcoffset()
        == end.utcoffset()
        == datetime.fromisoformat("2026-04-29T00:00:00+02:00").utcoffset()
    )


def test_a_date_range_runs_from_the_first_day_to_the_end_of_the_last():
    sample = active_shipment()
    sample["estimatedDelivery"] = {
        "startDate": "2026-04-29",
        "startTime": None,
        "endDate": "2026-04-30",
        "endTime": None,
    }
    parcel = normalize_parcel(sample)
    assert parcel["planned_from"] == "2026-04-29T00:00:00+02:00"
    assert parcel["planned_to"] == "2026-04-30T23:59:59+02:00"
