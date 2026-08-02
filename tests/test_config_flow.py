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
    CONF_REFRESH_INTERVAL,
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


def test_valid_tracking_code_bounds():
    """Deliberately permissive — Post's own 400 is the real authority."""
    assert valid_tracking_code("123456789012345678")  # 18 digits
    assert valid_tracking_code("RR123456789AT")  # UPU S10
    assert not valid_tracking_code("123")  # too short
    assert not valid_tracking_code("1" * 31)  # too long


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
    interval="30",
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
        "polling": {CONF_REFRESH_INTERVAL: interval},
    }


async def test_options_add_parcel(hass):
    entry = _hub([])
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with _accepts_any_code():
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _init_input(add="123456789012345678")
        )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [
        {CONF_TRACKING_CODE: "123456789012345678"}
    ]


async def test_options_add_code_with_separators(hass):
    """Pasted codes with spaces/dashes are sanitised like the consumer site."""
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with _accepts_any_code():
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _init_input(add="rr 123456789-at")
        )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: "RR123456789AT"}]


async def test_options_add_invalid_tracking_code(hass):
    """Rejected offline — too short to be any tracking number at all."""
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _init_input(add="123")
    )
    assert result["errors"]["base"] == "invalid_tracking_code"


async def test_options_add_code_post_itself_rejects(hass):
    """Plausible offline, but Post answers 400 — the API is the authority."""
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch(
        GET_PARCEL, new=AsyncMock(side_effect=OesterreichischePostInvalidCodeError("x"))
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _init_input(add="999999999999999999")
        )
    assert result["errors"]["base"] == "invalid_tracking_code"


async def test_options_add_parcel_while_post_is_down(hass):
    """Undecided means accept — an outage must not block adding a parcel."""
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch(GET_PARCEL, new=AsyncMock(side_effect=aiohttp.ClientError("boom"))):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _init_input(add="123456789012345678")
        )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [
        {CONF_TRACKING_CODE: "123456789012345678"}
    ]


async def test_options_add_duplicate_rejected(hass):
    entry = _hub([{CONF_TRACKING_CODE: "111111111111111111"}])
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _init_input(add="111111111111111111", remove=[])
    )
    assert result["errors"]["base"] == "already_tracked"


async def test_options_remove_parcel(hass):
    entry = _hub([
        {CONF_TRACKING_CODE: "111111111111111111"},
        {CONF_TRACKING_CODE: "222222222222222222"},
    ])
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _init_input(remove=["111111111111111111"])
    )
    assert result["type"] == "create_entry"
    codes = {p[CONF_TRACKING_CODE] for p in result["data"][CONF_PARCELS]}
    assert codes == {"222222222222222222"}


async def test_options_remove_then_readd_same_code(hass):
    """Remove-then-add order: re-adding a just-removed code works."""
    entry = _hub([{CONF_TRACKING_CODE: "111111111111111111"}])
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with _accepts_any_code():
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            _init_input(add="111111111111111111", remove=["111111111111111111"]),
        )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: "111111111111111111"}]


async def test_options_changes_interval_history_and_delivered(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _init_input(
            interval="120",
            history=True, filter_type="parcels", amount=5,
        ),
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_REFRESH_INTERVAL] == 120
    assert result["data"][CONF_INCLUDE_HISTORY] is True
    assert result["data"][CONF_DELIVERED_FILTER_TYPE] == "parcels"
    assert result["data"][CONF_DELIVERED_FILTER_AMOUNT] == 5
