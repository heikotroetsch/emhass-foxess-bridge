"""Tests for the config flow: pick the FoxESS device, name the EMHASS sensors."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.emhass_foxess_bridge.const import (
    CONF_DEVICE_ID,
    CONF_EMHASS_ENTITY,
    CONF_EMHASS_GRID_ENTITY,
    DOMAIN,
)

from .fake_foxess_openapi import DEVICE_NAME, SN, FakeFoxessOpenApi

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_user_flow_creates_entry_for_the_selected_device(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    with patch(
        "custom_components.emhass_foxess_bridge.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_DEVICE_ID: fox.device_id,
                CONF_EMHASS_ENTITY: "sensor.p_batt_forecast",
                CONF_EMHASS_GRID_ENTITY: "sensor.p_grid_forecast",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == SN
    assert result["data"][CONF_DEVICE_ID] == fox.device_id
    assert result["title"] == f"EMHASS bridge {DEVICE_NAME}"


async def test_flow_aborts_when_foxess_openapi_is_missing(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "foxess_openapi_missing"


async def test_unknown_device_is_rejected(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEVICE_ID: "not-a-device",
            CONF_EMHASS_ENTITY: "sensor.p_batt_forecast",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "device_invalid"}


async def test_same_inverter_cannot_be_added_twice(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    with patch(
        "custom_components.emhass_foxess_bridge.async_setup_entry", return_value=True
    ):
        for _ in range(2):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_DEVICE_ID: fox.device_id,
                    CONF_EMHASS_ENTITY: "sensor.p_batt_forecast",
                },
            )
            await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
