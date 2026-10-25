"""Tests for the diagnostics download used in issue reports."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.components.diagnostics.const import REDACTED
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.emhass_foxess_bridge.const import CONF_MIN_WINDOW_MINUTES

from .fake_foxess_openapi import LIVE, SN, FakeFoxessOpenApi, setup_bridge

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.typing import ClientSessionGenerator


async def test_diagnostics_describe_the_bridge_without_secrets(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    fox: FakeFoxessOpenApi,
) -> None:
    # The access token behind hass_client is issued at the real "now", so this
    # test plans relative to now instead of moving the clock to the fixture.
    await hass.config.async_set_time_zone("Europe/Berlin")
    now = dt_util.now().replace(second=0, microsecond=0)
    step = now + timedelta(minutes=15 - now.minute % 15)
    hass.states.async_set(
        "sensor.p_batt_forecast",
        "-5000.00",
        {
            "battery_scheduled_power": [
                {"date": step.isoformat(sep=" "), "p_batt_forecast": "-5000.0"}
            ]
        },
    )
    hass.states.async_set(
        "sensor.p_grid_forecast",
        "5400.00",
        {"forecasts": [{"date": step.isoformat(sep=" "), "p_grid_forecast": "5400.0"}]},
    )
    entry = await setup_bridge(hass, fox, {**LIVE, CONF_MIN_WINDOW_MINUTES: 15})

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics["entry"]["options"] == {**LIVE, CONF_MIN_WINDOW_MINUTES: 15}
    assert diagnostics["device"]["serial"] == REDACTED
    assert diagnostics["device"]["entity_ids"]["schedule"] == fox.entity_ids["schedule"]
    planned = diagnostics["schedule"]["planned"]
    assert len(planned) == 1
    assert (planned[0]["startHour"], planned[0]["startMinute"]) == (
        step.hour,
        step.minute,
    )
    assert planned[0]["workMode"] == "ForceCharge"
    assert diagnostics["schedule"]["active"] == planned
    assert diagnostics["schedule"]["last_result"] == "written"
    assert diagnostics["schedule"]["plan_stale"] is False
    assert diagnostics["schedule"]["min_soc_on_grid"] == 10
    assert diagnostics["schedule"]["api_remaining"] == 1200
    fox_schedule = diagnostics["foxess_openapi"]["schedule"]
    assert fox_schedule["attributes"]["master_switch"] is True
    plan = diagnostics["emhass"]["sensor.p_batt_forecast"]
    assert plan["state"] == "-5000.00"
    assert plan["available"] is True
    assert len(plan["attributes"]["battery_scheduled_power"]) == 1

    dumped = json.dumps(diagnostics)
    assert SN not in dumped
