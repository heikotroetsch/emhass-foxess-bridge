"""Tests for the FoxESS Open API client against recorded responses."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import aiohttp
import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.emhass_foxess_bridge.api import (
    BASE_URL,
    PATH_DEVICE_DETAIL,
    PATH_REAL_QUERY,
    PATH_SCHEDULER_ENABLE,
    PATH_SCHEDULER_GET,
    FoxApiAuthError,
    FoxApiCommunicationError,
    FoxApiDeviceNotFoundError,
    FoxApiRateLimitError,
    FoxApiResponseError,
    FoxOpenApiClient,
    build_signature,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )

SN = "60SH5TEST000001"
DETAIL_URL = f"{BASE_URL}{PATH_DEVICE_DETAIL}?sn={SN}"


@pytest.fixture
async def client(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
) -> FoxOpenApiClient:
    """Client bound to Home Assistant's aiohttp session, intercepted by the mocker."""
    return FoxOpenApiClient("key", async_get_clientsession(hass))


def test_signature_follows_documented_scheme() -> None:
    """Signature is md5 of path, token and timestamp joined by CRLF."""
    expected = hashlib.md5(  # noqa: S324
        b"/op/v1/device/detail\r\nsecret\r\n1700000000000"
    ).hexdigest()
    assert build_signature("/op/v1/device/detail", "secret", 1700000000000) == expected


async def test_requests_carry_auth_headers(
    client: FoxOpenApiClient,
    aioclient_mock: AiohttpClientMocker,
    load_fixture: Callable,
) -> None:
    """Every call sends token, timestamp, signature and lang headers."""
    aioclient_mock.get(DETAIL_URL, json=load_fixture("device_detail.json"))
    await client.async_get_device_detail(SN)
    headers = aioclient_mock.mock_calls[0][3]
    assert headers["token"] == "key"  # noqa: S105
    assert headers["lang"] == "en"
    assert headers["signature"] == build_signature(
        PATH_DEVICE_DETAIL, "key", headers["timestamp"]
    )


async def test_device_detail_is_parsed(
    client: FoxOpenApiClient,
    aioclient_mock: AiohttpClientMocker,
    load_fixture: Callable,
) -> None:
    """Device detail returns the result object with firmware and scheduler flag."""
    aioclient_mock.get(DETAIL_URL, json=load_fixture("device_detail.json"))
    detail = await client.async_get_device_detail(SN)
    assert detail["deviceType"] == "H3-5.0-E"
    assert detail["function"]["scheduler"] is True


async def test_real_time_values_are_flattened(
    client: FoxOpenApiClient,
    aioclient_mock: AiohttpClientMocker,
    load_fixture: Callable,
) -> None:
    """The nested datas list becomes a flat variable -> value mapping."""
    aioclient_mock.post(
        f"{BASE_URL}{PATH_REAL_QUERY}", json=load_fixture("real_query.json")
    )
    values = await client.async_get_real_time(SN, ["SoC", "loadsPower"])
    assert values["SoC"] == pytest.approx(9.0)
    assert values["loadsPower"] == pytest.approx(0.681)
    assert values["time"].startswith("2026-10-25 19:14")
    assert aioclient_mock.mock_calls[0][2] == {
        "sns": [SN],
        "variables": ["SoC", "loadsPower"],
    }


async def test_scheduler_get_without_groups_is_empty(
    client: FoxOpenApiClient,
    aioclient_mock: AiohttpClientMocker,
    load_fixture: Callable,
) -> None:
    """A null result (no schedule configured) yields an empty list."""
    aioclient_mock.post(
        f"{BASE_URL}{PATH_SCHEDULER_GET}",
        json=load_fixture("scheduler_get_empty.json"),
    )
    assert await client.async_get_scheduler(SN) == []


async def test_scheduler_get_returns_groups(
    client: FoxOpenApiClient,
    aioclient_mock: AiohttpClientMocker,
    load_fixture: Callable,
) -> None:
    """Configured groups are returned as a list."""
    aioclient_mock.post(
        f"{BASE_URL}{PATH_SCHEDULER_GET}",
        json=load_fixture("scheduler_get_groups.json"),
    )
    groups = await client.async_get_scheduler(SN)
    assert [g["workMode"] for g in groups] == ["ForceCharge", "SelfUse"]


async def test_rate_limit_errno_raises_dedicated_error(
    client: FoxOpenApiClient,
    aioclient_mock: AiohttpClientMocker,
    load_fixture: Callable,
) -> None:
    """errno 40400 maps to FoxApiRateLimitError."""
    aioclient_mock.get(DETAIL_URL, json=load_fixture("rate_limited.json"))
    with pytest.raises(FoxApiRateLimitError) as excinfo:
        await client.async_get_device_detail(SN)
    assert excinfo.value.errno == 40400


async def test_other_errno_raises_response_error(
    client: FoxOpenApiClient,
    aioclient_mock: AiohttpClientMocker,
    load_fixture: Callable,
) -> None:
    """Any other non-zero errno maps to FoxApiResponseError carrying the errno."""
    aioclient_mock.post(
        f"{BASE_URL}{PATH_SCHEDULER_ENABLE}", json=load_fixture("error_generic.json")
    )
    with pytest.raises(FoxApiResponseError) as excinfo:
        await client.async_set_scheduler_groups(SN, [])
    assert excinfo.value.errno == 41200


async def test_http_401_raises_auth_error(
    client: FoxOpenApiClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """An invalid key or signature is answered with HTTP 401."""
    aioclient_mock.get(DETAIL_URL, status=401)
    with pytest.raises(FoxApiAuthError):
        await client.async_get_device_detail(SN)


async def test_unknown_serial_raises_device_not_found(
    client: FoxOpenApiClient,
    aioclient_mock: AiohttpClientMocker,
    load_fixture: Callable,
) -> None:
    """errno 41930 maps to FoxApiDeviceNotFoundError."""
    aioclient_mock.get(DETAIL_URL, json=load_fixture("error_device_not_found.json"))
    with pytest.raises(FoxApiDeviceNotFoundError) as excinfo:
        await client.async_get_device_detail(SN)
    assert excinfo.value.errno == 41930


async def test_network_failure_raises_communication_error(
    client: FoxOpenApiClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Transport problems surface as FoxApiCommunicationError."""
    aioclient_mock.get(DETAIL_URL, exc=aiohttp.ClientConnectionError())
    with pytest.raises(FoxApiCommunicationError):
        await client.async_get_device_detail(SN)
