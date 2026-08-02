"""Tests for the Österreichische Post API client."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.oesterreichische_post.api import (
    OesterreichischePostApiClient,
    OesterreichischePostApiError,
    OesterreichischePostInvalidCodeError,
)

from .payloads import (
    ACTIVE_CODE,
    INVALID_CODE,
    active_shipment,
    graphql_response,
    invalid_code_response,
    not_found_response,
    server_error_response,
)


def _session_returning(status: int, body: object = None) -> MagicMock:
    response = AsyncMock()
    response.status = status
    if isinstance(body, str):
        response.json = AsyncMock(side_effect=json.JSONDecodeError("x", body, 0))
    else:
        response.json = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post = MagicMock(return_value=ctx)
    return session


async def test_get_parcel_returns_shipment_on_success():
    session = _session_returning(200, graphql_response(active_shipment()))
    client = OesterreichischePostApiClient(session)

    parcel = await client.async_get_parcel(ACTIVE_CODE)

    assert parcel["activeIdentityCode"] == ACTIVE_CODE
    # The number travels as a GraphQL *variable* — inline arguments are refused
    # by the endpoint, so this is a contract, not a style choice.
    request = session.post.call_args.kwargs["json"]
    assert request["variables"] == {"id": ACTIVE_CODE}
    assert "einzelsendung" in request["query"]


async def test_get_parcel_returns_none_when_not_found():
    """An unknown or not-yet-scanned number is a normal state, not an error."""
    client = OesterreichischePostApiClient(_session_returning(200, not_found_response()))
    assert await client.async_get_parcel(ACTIVE_CODE) is None


async def test_get_parcel_raises_invalid_code_on_malformed_number():
    """The 400 is what lets the config flow reject a typo without a regex."""
    client = OesterreichischePostApiClient(
        _session_returning(400, invalid_code_response())
    )
    with pytest.raises(OesterreichischePostInvalidCodeError):
        await client.async_get_parcel(INVALID_CODE)


async def test_invalid_code_error_is_an_api_error():
    """So the coordinator's existing except clause keeps catching it."""
    assert issubclass(
        OesterreichischePostInvalidCodeError, OesterreichischePostApiError
    )


async def test_get_parcel_raises_on_graphql_errors():
    """Schema drift must raise, so the coordinator keeps its cached payload."""
    client = OesterreichischePostApiClient(
        _session_returning(200, server_error_response())
    )
    with pytest.raises(OesterreichischePostApiError) as err:
        await client.async_get_parcel(ACTIVE_CODE)
    assert "Cannot query field" in str(err.value)


async def test_get_parcel_raises_on_errors_without_message():
    client = OesterreichischePostApiClient(
        _session_returning(500, {"errors": [{"extensions": {"code": "BOOM"}}, "junk"]})
    )
    with pytest.raises(OesterreichischePostApiError) as err:
        await client.async_get_parcel(ACTIVE_CODE)
    assert "GraphQL error" in str(err.value)


async def test_get_parcel_raises_on_non_list_errors():
    client = OesterreichischePostApiClient(
        _session_returning(500, {"errors": "everything is broken"})
    )
    with pytest.raises(OesterreichischePostApiError):
        await client.async_get_parcel(ACTIVE_CODE)


async def test_get_parcel_raises_on_error_status_without_errors():
    client = OesterreichischePostApiClient(_session_returning(503, {}))
    with pytest.raises(OesterreichischePostApiError) as err:
        await client.async_get_parcel(ACTIVE_CODE)
    assert "503" in str(err.value)


async def test_get_parcel_raises_when_data_is_missing():
    client = OesterreichischePostApiClient(_session_returning(200, {"extensions": {}}))
    with pytest.raises(OesterreichischePostApiError):
        await client.async_get_parcel(ACTIVE_CODE)


async def test_get_parcel_returns_none_on_unexpected_shipment_shape(caplog):
    """A reshaped einzelsendung must not fail the poll for every other parcel."""
    client = OesterreichischePostApiClient(
        _session_returning(200, graphql_response(["not", "an", "object"]))
    )
    assert await client.async_get_parcel(ACTIVE_CODE) is None
    assert "unexpected shipment shape" in caplog.text


async def test_get_parcel_raises_on_unparseable_body():
    client = OesterreichischePostApiClient(_session_returning(200, "not json"))
    with pytest.raises(OesterreichischePostApiError):
        await client.async_get_parcel(ACTIVE_CODE)


async def test_get_parcel_raises_on_non_object_body():
    client = OesterreichischePostApiClient(
        _session_returning(200, ["not", "a", "dict"])
    )
    with pytest.raises(OesterreichischePostApiError):
        await client.async_get_parcel(ACTIVE_CODE)


async def test_get_parcel_propagates_network_error():
    """ClientError is left alone — DataUpdateCoordinator already wraps it."""
    session = MagicMock()
    session.post = MagicMock(side_effect=aiohttp.ClientError("boom"))
    client = OesterreichischePostApiClient(session)
    with pytest.raises(aiohttp.ClientError):
        await client.async_get_parcel(ACTIVE_CODE)
