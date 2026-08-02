"""Österreichische Post public tracking API client.

One keyless GraphQL POST per tracking code. The contract the coordinator
relies on:

* ``async_get_parcel`` returns the raw ``einzelsendung`` dict on success,
* returns ``None`` when Post says the tracking code is unknown or not yet
  scanned (a normal, expected state — never an error),
* raises :class:`OesterreichischePostInvalidCodeError` when Post rejects the
  *format* of the number, so the config flow and the ``track_parcel`` service
  can tell "you typed nonsense" apart from "not scanned yet",
* raises :class:`OesterreichischePostApiError` for anything else,
* lets ``aiohttp.ClientError`` propagate untouched — ``DataUpdateCoordinator``
  already wraps those into ``UpdateFailed``.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import ERROR_INVALID_IDENTITY_CODE, TRACKING_API_URL, TRACKING_QUERY

_LOGGER = logging.getLogger(__name__)


class OesterreichischePostApiError(Exception):
    """Raised when an Österreichische Post API call returns an unexpected response."""

    def __init__(self, detail: str) -> None:
        """Store the status code that triggered the error."""
        super().__init__(f"Österreichische Post API request failed: {detail}")
        self.detail = detail


class OesterreichischePostInvalidCodeError(OesterreichischePostApiError):
    """Raised when Post rejects the tracking number as malformed.

    A separate class on purpose: this is the one failure a *user* can fix, and
    the only one that must not be retried on the next poll with the same input.
    """


class OesterreichischePostApiClient:
    """Client for the public Österreichische Post tracking endpoint.

    No authentication — the endpoint is keyed on the tracking number alone, and
    unlike some siblings in the suite it checks neither ``Origin`` nor
    ``Referer``. It answers a plain GraphQL envelope::

        {"data": {"einzelsendung": {...}}}    a known parcel
        {"data": {"einzelsendung": null}}     unknown / not yet scanned
        {"errors": [{...}]}                   HTTP 400 malformed, or a fault
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's tracking details.

        Returns the shipment dict for a known parcel, or ``None`` when Post
        reports the number as unknown — which is also what a not-yet-scanned
        parcel gets. A malformed number raises
        :class:`OesterreichischePostInvalidCodeError`; any other envelope or
        status raises :class:`OesterreichischePostApiError`; network errors
        propagate as ``aiohttp.ClientError``.
        """
        request = {"query": TRACKING_QUERY, "variables": {"id": tracking_code}}
        async with self._session.post(TRACKING_API_URL, json=request) as response:
            status = response.status
            try:
                # content_type=None: the error responses in particular have been
                # seen served as text/plain, which aiohttp would refuse to parse.
                payload = await response.json(content_type=None)
            except ValueError as err:
                raise OesterreichischePostApiError(
                    f"unparseable body ({err})"
                ) from err

        if not isinstance(payload, dict):
            raise OesterreichischePostApiError("unexpected body (not a JSON object)")

        errors = payload.get("errors")
        if errors:
            if _is_invalid_code(errors):
                raise OesterreichischePostInvalidCodeError(
                    f"'{tracking_code}' is not a valid tracking number"
                )
            raise OesterreichischePostApiError(_error_detail(errors, status))

        if status != 200:
            raise OesterreichischePostApiError(f"HTTP {status}")

        data = payload.get("data")
        if not isinstance(data, dict):
            raise OesterreichischePostApiError("response carried no data object")

        shipment = data.get("einzelsendung")
        if shipment is None:
            return None
        if not isinstance(shipment, dict):
            # A non-null einzelsendung that is not an object means the schema
            # moved under us; treat it as unknown rather than failing the poll
            # for every other parcel.
            _LOGGER.warning(
                "Österreichische Post returned an unexpected shipment shape for %s",
                tracking_code,
            )
            return None
        return shipment


def _is_invalid_code(errors: Any) -> bool:
    """Whether ``errors`` is Post's "malformed tracking number" rejection."""
    if not isinstance(errors, list):
        return False
    for error in errors:
        if not isinstance(error, dict):
            continue
        extensions = error.get("extensions")
        if (
            isinstance(extensions, dict)
            and extensions.get("code") == ERROR_INVALID_IDENTITY_CODE
        ):
            return True
    return False


def _error_detail(errors: Any, status: int) -> str:
    """Summarise a GraphQL ``errors[]`` block for the exception message."""
    if isinstance(errors, list):
        messages = [
            str(error.get("message"))
            for error in errors
            if isinstance(error, dict) and error.get("message")
        ]
        if messages:
            return f"HTTP {status}: {'; '.join(messages)}"
    return f"HTTP {status}: GraphQL error"
