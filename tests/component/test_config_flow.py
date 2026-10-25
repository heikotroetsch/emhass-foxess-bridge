"""Tests for the config flow."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.emhass_foxess_bridge.api import (
    FoxApiAuthError,
    FoxApiCommunicationError,
    FoxApiDeviceNotFoundError,
)
from custom_components.emhass_foxess_bridge.const import (
    CONF_API_KEY,
    CONF_DEVICE_SN,
    CONF_EMHASS_ENTITY,
    DOMAIN,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

SN = "60SH5TEST000001"
USER_INPUT = {
    CONF_API_KEY: "key",
    CONF_DEVICE_SN: f" {SN} ",
    CONF_EMHASS_ENTITY: "sensor.p_batt_forecast",
}
DETAIL_PATCH = (
    "custom_components.emhass_foxess_bridge.config_flow."
    "FoxOpenApiClient.async_get_device_detail"
)


async def test_user_flow_creates_entry(
    hass: HomeAssistant, load_fixture: Callable
) -> None:
    """Valid credentials create an entry keyed by the trimmed serial."""
    detail = load_fixture("device_detail.json")["result"]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    with (
        patch(DETAIL_PATCH, return_value=detail),
        patch(
            "custom_components.emhass_foxess_bridge.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == SN
    assert result["data"][CONF_DEVICE_SN] == SN
    assert result["title"] == f"FoxESS H3-5.0-E {SN}"


async def test_inverter_without_scheduler_is_rejected(
    hass: HomeAssistant, load_fixture: Callable
) -> None:
    """An inverter that cannot be scheduled shows the no_scheduler error."""
    detail = load_fixture("device_detail.json")["result"]
    detail["function"] = {"scheduler": False}
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(DETAIL_PATCH, return_value=detail):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_scheduler"}


async def test_connection_problem_is_reported(hass: HomeAssistant) -> None:
    """Transport errors map to cannot_connect."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(DETAIL_PATCH, side_effect=FoxApiCommunicationError("boom")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_invalid_key_is_reported(hass: HomeAssistant) -> None:
    """HTTP 401 from FoxESS maps to invalid_auth."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(DETAIL_PATCH, side_effect=FoxApiAuthError("401")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["errors"] == {"base": "invalid_auth"}


async def test_unknown_serial_is_reported(hass: HomeAssistant) -> None:
    """errno 41930 maps to device_not_found."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(
        DETAIL_PATCH, side_effect=FoxApiDeviceNotFoundError("nope", errno=41930)
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["errors"] == {"base": "device_not_found"}
