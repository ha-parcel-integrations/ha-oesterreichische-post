"""Sample Österreichische Post API payloads shared by the test modules.

The two *error* shapes — the not-found 200 and the malformed-number 400 — are
captured verbatim from the live endpoint. The populated shipment is hand-built
from the introspected schema: no real Austrian parcel has passed through this
integration yet, so nothing here proves which fields Post actually fills in.
That is exactly what the one-shot WARNINGs in ``parcels.py`` are for.

Field names, the event ordering (**newest-first**) and the camelCase state keys
are all confirmed against the schema and the carrier's own app.

Kept in one module rather than inline per test: when the populated shape turns
out to differ from what we assumed, there is then exactly one place to fix.
"""
from __future__ import annotations

# 10-22 digits and UPU S10 are both accepted by the endpoint; the suite uses one
# of each so the permissive regex is exercised both ways.
ACTIVE_CODE = "123456789012345678"
DELIVERED_CODE = "234567890123456789"
S10_CODE = "RR123456789AT"
# Short enough that Post answers 400 INVALID_ARGUMENT_IDENTITY_CODE.
INVALID_CODE = "123"


def event(
    state_key: str,
    timestamp: str,
    text_en: str,
    *,
    text: str | None = None,
    place: str = "Wien",
) -> dict:
    """One entry of Post's ``sendungsEvents`` timeline.

    ``trackingStateKey`` is camelCase on the wire; ``timestamp`` carries no
    offset, because Post stamps in Austrian local time.
    """
    return {
        "trackingStateKey": state_key,
        "trackingState": text_en.upper().replace(" ", "_"),
        "trackingDesc": text_en,
        "text": text or text_en,
        "textEn": text_en,
        "timestamp": timestamp,
        "eventcountry": "AT",
        "eventpostalcode": "1010",
        "eventPlaceName": place,
        "stateInfo": {"titleText": text_en, "descText": text_en},
    }


def delivered_shipment(code: str = DELIVERED_CODE) -> dict:
    """A delivered parcel — the ``einzelsendung`` object, newest event first."""
    return {
        "activeIdentityCode": code,
        "originalIdentityCode": code,
        "status": "Delivered",
        "produktkategorie": "PAKET",
        "productType": "PAKET_NATIONAL",
        "weight": 1.25,
        "branchkey": None,
        "deliveryType": "DELIVERY",
        "deliveryAddressText": "",
        "estimatedDeliveryDate": None,
        "estimatedDeliveryDateText": None,
        "estimatedDelivery": None,
        "dimensions": {"height": 10, "length": 30, "width": 20},
        "shipper": {
            "name": "Example Shop",
            "postalCode": "1010",
            "city": "Wien",
            "country": "AT",
        },
        "sendungsEvents": [
            event("delivered", "2026-04-29T13:12:42", "Delivered"),
            event("inDelivery", "2026-04-29T08:46:00", "Out for delivery"),
            event("inDistribution", "2026-04-28T15:52:17", "In distribution"),
            event("pendingInformation", "2026-04-27T23:03:58", "Shipment announced"),
        ],
        "customsInformation": {
            "customsDocumentAvailable": False,
            "userDocumentNeeded": False,
        },
    }


def active_shipment(code: str = ACTIVE_CODE) -> dict:
    """An out-for-delivery parcel with a real ETA window."""
    shipment = delivered_shipment(code)
    shipment.update(
        {
            "status": "Out for delivery",
            "estimatedDelivery": {
                "startDate": "2026-04-29",
                "startTime": "13:00",
                "endDate": "2026-04-29",
                "endTime": "15:00",
            },
            "sendungsEvents": shipment["sendungsEvents"][1:],
        }
    )
    return shipment


def pickup_shipment(code: str = ACTIVE_CODE) -> dict:
    """A parcel waiting at a post partner, keyed by ``branchkey``."""
    shipment = active_shipment(code)
    shipment.update(
        {
            "status": "Ready for collection",
            "branchkey": "1010-04",
            "deliveryType": "REDIRECTBRANCH",
            "sendungsEvents": [
                event("readyForPickUp", "2026-04-29T11:20:00", "Ready for collection"),
                *shipment["sendungsEvents"],
            ],
        }
    )
    return shipment


def graphql_response(shipment: dict | None) -> dict:
    """Wrap a shipment in Post's plain GraphQL envelope."""
    return {"data": {"einzelsendung": shipment}}


def not_found_response() -> dict:
    """HTTP 200 for an unknown or not-yet-scanned number. Captured live."""
    return {"data": {"einzelsendung": None}}


def invalid_code_response() -> dict:
    """HTTP 400 for a malformed number. Captured live."""
    return {
        "errors": [
            {
                "message": "Invalid argument Identity code",
                "extensions": {"code": "INVALID_ARGUMENT_IDENTITY_CODE"},
            }
        ]
    }


def server_error_response() -> dict:
    """HTTP 200 carrying a top-level GraphQL error — schema drift or a fault."""
    return {
        "errors": [{"message": "Cannot query field 'nope' on type 'Query'"}],
        "data": None,
    }
