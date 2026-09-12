"""Tests for the Österreichische Post config and options flow."""
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.oesterreichische_post.api import (
    OesterreichischePostApiError,
    OesterreichischePostInvalidCodeError,
)
from custom_components.oesterreichische_post.config_flow import (
    async_verify_tracking_code,
    normalize_tracking_code,
    valid_tracking_code,
)
from custom_components.oesterreichische_post.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_PARCELS,
    CONF_TRACKING_CODE,
    DOMAIN,
)

GET_PARCEL = (
    "custom_components.oesterreichische_post.api."
    "OesterreichischePostApiClient.async_get_parcel"
)


def _accepts_any_code():
    """Patch the endpoint into accepting whatever the flow sends it."""
    return patch(GET_PARCEL, new=AsyncMock(return_value=None))


def test_normalize_tracking_code_strips_and_uppercases():
    assert normalize_tracking_code("1234 5678-9012") == "123456789012"
    assert normalize_tracking_code("rr123456789at") == "RR123456789AT"
    assert normalize_tracking_code("") == ""
    assert normalize_tracking_code(None) == ""


def test_valid_tracking_code_accepts_any_non_empty_value():
    """Post's own 400 is the real authority, not a guessed client-side shape."""
    assert valid_tracking_code("123456789012345678")  # 18 digits
    assert valid_tracking_code("RR123456789AT")  # UPU S10
    assert valid_tracking_code("123")
    assert valid_tracking_code("1" * 31)
    assert not valid_tracking_code("")


async def test_verify_accepts_a_number_post_does_not_know_yet(hass):
    """A printed-but-unscanned label is the normal state of a new parcel."""
    with patch(GET_PARCEL, new=AsyncMock(return_value=None)):
        assert await async_verify_tracking_code(hass, "123456789012345678") is True


async def test_verify_rejects_only_a_malformed_number(hass):
    with patch(
        GET_PARCEL, new=AsyncMock(side_effect=OesterreichischePostInvalidCodeError("x"))
    ):
        assert await async_verify_tracking_code(hass, "123") is False


@pytest.mark.parametrize(
    "error", [OesterreichischePostApiError("HTTP 503"), aiohttp.ClientError("boom")]
)
async def test_verify_is_undecided_when_post_is_unreachable(hass, caplog, error):
    """An outage must not stop someone adding a parcel they know is valid."""
    with patch(GET_PARCEL, new=AsyncMock(side_effect=error)):
        assert await async_verify_tracking_code(hass, "123456789012345678") is None
    assert "Could not verify" in caplog.text


async def test_user_flow_creates_hub_without_input(hass):
    """No account, no postcode — the entry is created straight away."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "Österreichische Post"
    assert result["options"][CONF_PARCELS] == []


async def test_second_hub_rejected(hass):
    MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "abort"
    # single_config_entry in the manifest aborts before the flow runs.
    assert result["reason"] == "single_instance_allowed"


def _hub(parcels: list[dict]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={CONF_PARCELS: parcels},
    )


def _init_input(
    *, add="", remove=None, history=False,
    filter_type="days", amount=7,
) -> dict:
    """Build the sectioned options-form submission."""
    parcels: dict = {"add": add}
    if remove is not None:
        parcels["remove"] = remove
    return {
        "parcels": parcels,
        "delivered": {
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        "history": {CONF_INCLUDE_HISTORY: history},
    }


async def _open_options_step(hass, entry, step_id: str):
    """Start the options flow and select one of its two top-level routes."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert result["menu_options"] == ["parcels", "settings"]
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step_id}
    )


async def test_options_parcel_list_can_be_cleared(hass):
    """A submitted empty list removes the final manually tracked parcel."""
    entry = MockConfigEntry(domain=DOMAIN, options={CONF_PARCELS: [{CONF_TRACKING_CODE: "EXAMPLE111111"}]})
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": []}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == []


async def test_options_settings_preserve_parcel_list(hass):
    """Saving settings must never replace the manually tracked parcel list."""
    parcels = [{CONF_TRACKING_CODE: "EXAMPLE111111"}]
    entry = MockConfigEntry(domain=DOMAIN, options={CONF_PARCELS: parcels})
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "settings")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_DELIVERED_FILTER_TYPE: "days", CONF_DELIVERED_FILTER_AMOUNT: 7, CONF_INCLUDE_HISTORY: False}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == parcels


async def test_options_parcel_list_rejects_a_code_post_reports_invalid(hass):
    """Post's own 400 is what stops a malformed code being added."""
    entry = MockConfigEntry(domain=DOMAIN, options={CONF_PARCELS: []})
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    with patch(
        GET_PARCEL, new=AsyncMock(side_effect=OesterreichischePostInvalidCodeError("x"))
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"tracking_codes": ["123456789012345678"]}
        )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_tracking_code"}
