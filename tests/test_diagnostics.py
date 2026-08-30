"""Tests for Österreichische Post diagnostics."""
from datetime import timedelta
from unittest.mock import MagicMock

from custom_components.oesterreichische_post.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redacts_and_counts(hass):
    """Diagnostics get pasted into public issues — nothing identifying may survive."""
    code = "123456789012345678"
    entry = MagicMock()
    entry.options = {"parcels": [{"tracking_code": code}]}
    entry.runtime_data.coordinator.data = [
        {
            "barcode": code,
            "sender": "Example Shop",
            "receiver": None,
            "status": "at_pickup_point",
            "pickup_point": "1010-04",
            "raw": {
                "activeIdentityCode": code,
                "originalIdentityCode": code,
                "branchkey": "1010-04",
                "deliveryAddressText": "Musterstrasse 1, 1010 Wien",
                "shipper": {"name": "Example Shop", "city": "Wien"},
                "produktkategorie": "PAKET",
                "sendungsEvents": [
                    {
                        "trackingStateKey": "readyForPickUp",
                        "eventPlaceName": "Wien",
                        "eventpostalcode": "1010",
                    }
                ],
            },
        }
    ]
    entry.runtime_data.coordinator.delivered = []
    entry.runtime_data.coordinator.current_tier_minutes = 45
    entry.runtime_data.coordinator.update_interval = timedelta(minutes=45)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"] == {"incoming_active": 1, "delivered": 0}
    assert result["polling"] == {
        "current_tier_minutes": 45,
        "update_interval_seconds": 2700.0,
    }
    # tracking codes and payload PII are redacted, at every nesting level
    assert result["entry_options"]["parcels"][0]["tracking_code"] == "**REDACTED**"
    assert result["incoming"][0]["barcode"] == "**REDACTED**"
    assert result["incoming"][0]["pickup_point"] == "**REDACTED**"
    raw = result["incoming"][0]["raw"]
    assert raw["activeIdentityCode"] == "**REDACTED**"
    assert raw["originalIdentityCode"] == "**REDACTED**"
    assert raw["branchkey"] == "**REDACTED**"
    assert raw["deliveryAddressText"] == "**REDACTED**"
    assert raw["shipper"] == "**REDACTED**"
    # The event trail traces the parcel past the user's own door.
    assert raw["sendungsEvents"][0]["eventPlaceName"] == "**REDACTED**"
    assert raw["sendungsEvents"][0]["eventpostalcode"] == "**REDACTED**"
    # non-identifying fields survive, or the diagnostics would be useless
    assert result["incoming"][0]["status"] == "at_pickup_point"
    assert raw["produktkategorie"] == "PAKET"
    assert raw["sendungsEvents"][0]["trackingStateKey"] == "readyForPickUp"
