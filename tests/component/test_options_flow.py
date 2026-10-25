"""Tests for the options flow: planner tunables without touching the switches."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.data_entry_flow import FlowResultType

from custom_components.emhass_foxess_bridge.const import (
    CONF_CHARGE_THRESHOLD_W,
    CONF_DRY_RUN,
    CONF_ENABLED,
    CONF_MAX_GROUPS,
    CONF_MIN_WINDOW_MINUTES,
    CONF_PLAN_MAX_AGE_HOURS,
)

from .fake_fox import LIVE, FakeFoxCloud, setup_bridge

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_options_flow_saves_tunables_and_keeps_the_switches(
    hass: HomeAssistant, fox: FakeFoxCloud
) -> None:
    entry = await setup_bridge(hass, LIVE)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_CHARGE_THRESHOLD_W: 250,
            CONF_MIN_WINDOW_MINUTES: 45,
            CONF_MAX_GROUPS: 6,
            CONF_PLAN_MAX_AGE_HOURS: 2,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_CHARGE_THRESHOLD_W] == 250
    assert entry.options[CONF_MIN_WINDOW_MINUTES] == 45
    assert entry.options[CONF_MAX_GROUPS] == 6
    assert entry.options[CONF_PLAN_MAX_AGE_HOURS] == 2
    assert entry.options[CONF_ENABLED] is True
    assert entry.options[CONF_DRY_RUN] is False


async def test_options_flow_shows_the_current_values(
    hass: HomeAssistant, fox: FakeFoxCloud
) -> None:
    entry = await setup_bridge(hass, {CONF_MIN_WINDOW_MINUTES: 15})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"].schema
    defaults = {str(key): key.default() for key in schema if key.default is not None}
    assert defaults[CONF_MIN_WINDOW_MINUTES] == 15
    assert defaults[CONF_CHARGE_THRESHOLD_W] == 100
    assert defaults[CONF_MAX_GROUPS] == 8
    assert defaults[CONF_PLAN_MAX_AGE_HOURS] == 3
