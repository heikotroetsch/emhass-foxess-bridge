"""Tests for entry setup and the sensor platform."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
CLIENT = "custom_components.emhass_foxess_bridge.FoxOpenApiClient"


async def test_setup_creates_live_sensors(
    hass: HomeAssistant, load_fixture: Callable
) -> None:
    """Setting up the entry exposes the real-time values as sensors."""
    detail = load_fixture("device_detail.json")["result"]
    realtime = {"SoC": 9.0, "loadsPower": 0.681, "pvPower": 0.0}
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SN,
        data={
            CONF_API_KEY: "key",
            CONF_DEVICE_SN: SN,
            CONF_EMHASS_ENTITY: "sensor.p_batt_forecast",
        },
    )
    entry.add_to_hass(hass)

    with (
        patch(f"{CLIENT}.async_get_device_detail", return_value=detail),
        patch(f"{CLIENT}.async_get_battery_soc", return_value={"minSocOnGrid": 10}),
        patch(f"{CLIENT}.async_get_real_time", return_value=realtime),
        patch(f"{CLIENT}.async_get_scheduler_flag", return_value={"enable": False}),
        patch(f"{CLIENT}.async_get_scheduler", return_value=[]),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    registry = er.async_get(hass)
    soc_entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{SN}_SoC")
    assert soc_entity_id is not None
    assert hass.states.get(soc_entity_id).state == "9.0"
    load_entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{SN}_loadsPower")
    assert hass.states.get(load_entity_id).state == "0.681"

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
