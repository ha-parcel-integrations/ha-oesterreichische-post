"""Diagnostics support for the Österreichische Post parcel tracker integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import OesterreichischePostConfigEntry

# Diagnostics are pasted into public issues, so redact anything that
# identifies a person, an address or a specific parcel. Over-redacting is
# cheap; under-redacting leaks a user's home address into a GitHub thread.
#
TO_REDACT = {
    # canonical fields we publish ourselves
    "tracking_code",
    "barcode",
    "sender",
    "receiver",
    "url",
    "pickup_point",
    # Post payload fields. The two identity codes are the tracking number
    # itself; branchkey names the collection point (so, roughly, where the user
    # lives); the event place/postcode pair traces the parcel past their door;
    # shipper is the sender's name and address block.
    "activeIdentityCode",
    "originalIdentityCode",
    "branchkey",
    "deliveryAddressText",
    "shipper",
    "eventpostalcode",
    "eventPlaceName",
    "postalCode",
    "postal_code",
    "city",
    "street",
    "email",
    "name",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: OesterreichischePostConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the Österreichische Post config entry."""
    coordinator = entry.runtime_data.coordinator

    return {
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "counts": {
            "incoming_active": len(coordinator.data or []),
            "delivered": len(coordinator.delivered or []),
        },
        "incoming": async_redact_data(coordinator.data or [], TO_REDACT),
        "delivered": async_redact_data(coordinator.delivered or [], TO_REDACT),
    }
