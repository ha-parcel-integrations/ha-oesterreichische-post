"""Tests for the Österreichische Post services (track_parcel / untrack_parcel)."""
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.oesterreichische_post.api import (
    OesterreichischePostInvalidCodeError,
)
from custom_components.oesterreichische_post.const import (
    CONF_PARCELS,
    CONF_TRACKING_CODE,
    DOMAIN,
)

from .payloads import active_shipment

_SAMPLE = active_shipment()



async def _setup(hass, parcels: list[dict] | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={CONF_PARCELS: parcels or []},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.oesterreichische_post.api.OesterreichischePostApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_track_parcel_adds_to_options(hass):
    entry = await _setup(hass)
    with patch(
        "custom_components.oesterreichische_post.api.OesterreichischePostApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: "123456789012345678"},
            blocking=True,
        )
        await hass.async_block_till_done()

    parcels = entry.options[CONF_PARCELS]
    assert parcels == [{CONF_TRACKING_CODE: "123456789012345678"}]


async def test_track_parcel_normalizes_code(hass):
    entry = await _setup(hass)
    with patch(
        "custom_components.oesterreichische_post.api.OesterreichischePostApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: "rr 123456789-at"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: "RR123456789AT"}]


async def test_track_parcel_rejects_blank_code(hass):
    """Nothing left after normalizing — rejected without asking Post."""
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "track_parcel", {CONF_TRACKING_CODE: "---"}, blocking=True
        )


async def test_track_parcel_rejects_code_post_itself_refuses(hass):
    """Plausible offline, but Post answers 400 — surfaced to the caller."""
    await _setup(hass)
    with patch(
        "custom_components.oesterreichische_post.api.OesterreichischePostApiClient.async_get_parcel",
        new=AsyncMock(side_effect=OesterreichischePostInvalidCodeError("nope")),
    ), pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: "999999999999999999"},
            blocking=True,
        )


async def test_track_parcel_duplicate_is_noop(hass):
    entry = await _setup(hass)
    with patch(
        "custom_components.oesterreichische_post.api.OesterreichischePostApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        for _ in range(2):
            await hass.services.async_call(
                DOMAIN,
                "track_parcel",
                {CONF_TRACKING_CODE: "123456789012345678"},
                blocking=True,
            )
            await hass.async_block_till_done()

    assert len(entry.options[CONF_PARCELS]) == 1


async def test_untrack_parcel_removes_from_options(hass):
    entry = await _setup(
        hass, parcels=[{CONF_TRACKING_CODE: "123456789012345678"}]
    )
    with patch(
        "custom_components.oesterreichische_post.api.OesterreichischePostApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        await hass.services.async_call(
            DOMAIN,
            "untrack_parcel",
            {CONF_TRACKING_CODE: "123456789012345678"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert entry.options[CONF_PARCELS] == []


async def test_untrack_unknown_code_is_noop(hass):
    entry = await _setup(
        hass, parcels=[{CONF_TRACKING_CODE: "123456789012345678"}]
    )
    with patch(
        "custom_components.oesterreichische_post.api.OesterreichischePostApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        await hass.services.async_call(
            DOMAIN,
            "untrack_parcel",
            {CONF_TRACKING_CODE: "000000000000000000"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert len(entry.options[CONF_PARCELS]) == 1
