"""Tests for Österreichische Post device triggers."""
from homeassistant.components import automation
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.oesterreichische_post.const import DOMAIN
from custom_components.oesterreichische_post.device_trigger import (
    TRIGGER_EVENTS,
    async_get_triggers,
)


async def test_get_triggers_returns_all_four(hass):
    triggers = await async_get_triggers(hass, "device123")
    types = {t["type"] for t in triggers}
    assert types == {
        "parcel_registered",
        "parcel_status_changed",
        "parcel_delivered",
        "parcel_delivery_time_changed",
    }
    for trigger in triggers:
        assert trigger["domain"] == DOMAIN
        assert trigger["device_id"] == "device123"


def test_trigger_events_map_to_domain_prefix():
    assert TRIGGER_EVENTS["parcel_registered"] == f"{DOMAIN}_parcel_registered"


async def test_device_trigger_fires_automation(hass):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
    )

    assert await async_setup_component(
        hass,
        automation.DOMAIN,
        {
            automation.DOMAIN: {
                "trigger": {
                    "platform": "device",
                    "domain": DOMAIN,
                    "device_id": device.id,
                    "type": "parcel_status_changed",
                },
                "action": {"event": "oesterreichische_post_test_fired"},
            }
        },
    )
    await hass.async_block_till_done()

    fired: list = []
    hass.bus.async_listen("oesterreichische_post_test_fired", lambda e: fired.append(e))

    hass.bus.async_fire(
        f"{DOMAIN}_parcel_status_changed",
        {"barcode": "A", "device_id": device.id},
    )
    await hass.async_block_till_done()
    assert len(fired) == 1

    hass.bus.async_fire(
        f"{DOMAIN}_parcel_status_changed",
        {"barcode": "B", "device_id": "some-other-device"},
    )
    await hass.async_block_till_done()
    assert len(fired) == 1
