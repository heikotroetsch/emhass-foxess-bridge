"""Tests for the manual services: apply_plan, clear_schedule, set_schedule."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.emhass_foxess_bridge.const import (
    CONF_ENABLED,
    DOMAIN,
    SERVICE_APPLY_PLAN,
    SERVICE_CLEAR_SCHEDULE,
    SERVICE_SET_SCHEDULE,
)

from .fake_foxess_openapi import (
    LIVE,
    NIGHT_GROUP,
    PLAN_TIME,
    SN,
    FakeFoxessOpenApi,
    publish_plan,
    settle,
    setup_bridge,
    state,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

DISCHARGE_GROUP = {
    "enable": 1,
    "startHour": 17,
    "startMinute": 0,
    "endHour": 18,
    "endMinute": 59,
    "workMode": "ForceDischarge",
    "minSocOnGrid": 10,
    "fdSoc": 20,
    "fdPwr": 3000,
    "maxSoc": 100,
}


async def call(hass: HomeAssistant, service: str, data: dict | None = None) -> None:
    await hass.services.async_call(DOMAIN, service, data or {}, blocking=True)
    await hass.async_block_till_done()


async def test_apply_plan_runs_the_loop_now(
    hass: HomeAssistant,
    fox: FakeFoxessOpenApi,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    publish_plan(hass, load_fixture)
    entry = await setup_bridge(hass, fox, LIVE)
    await settle(hass, freezer)
    reads = len(fox.service_calls("get_schedule"))

    await call(hass, SERVICE_APPLY_PLAN, {"config_entry_id": entry.entry_id})
    assert len(fox.service_calls("get_schedule")) == reads + 1


async def test_services_default_to_every_loaded_entry(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    await setup_bridge(hass, fox)
    reads = len(fox.service_calls("get_schedule"))
    await call(hass, SERVICE_APPLY_PLAN)
    assert len(fox.service_calls("get_schedule")) == reads + 1


async def test_unknown_entry_id_is_rejected(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    await setup_bridge(hass, fox)
    with pytest.raises(ServiceValidationError):
        await call(hass, SERVICE_APPLY_PLAN, {"config_entry_id": "does-not-exist"})


async def test_clear_schedule_disables_the_scheduler_and_the_bridge(
    hass: HomeAssistant,
    fox: FakeFoxessOpenApi,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    fox.groups = [NIGHT_GROUP]
    fox.flag_enabled = True
    fox.publish()
    publish_plan(hass, load_fixture)
    entry = await setup_bridge(hass, fox, LIVE)
    await settle(hass, freezer)
    assert state(hass, "sensor", "active_schedule").state == "ForceCharge 02:00-04:59"

    await call(hass, SERVICE_CLEAR_SCHEDULE)

    assert fox.service_calls("disable_scheduler") == [{"device_sn": SN}]
    assert entry.options[CONF_ENABLED] is False
    assert state(hass, "switch", "bridge_enabled").state == "off"
    assert state(hass, "sensor", "active_schedule").state == "none"
    assert fox.service_calls("set_schedule") == [], "the plan must not be re-applied"


async def test_set_schedule_writes_raw_groups(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    await setup_bridge(hass, fox)
    await call(
        hass,
        SERVICE_SET_SCHEDULE,
        {
            "groups": [
                {"start": "02:00", "end": "04:59", "work_mode": "ForceCharge"},
                {
                    "start": "17:00",
                    "end": "18:59",
                    "work_mode": "ForceDischarge",
                    "fd_soc": 20,
                    "fd_pwr": 3000,
                },
            ]
        },
    )
    assert fox.service_calls("set_schedule") == [
        {
            "device_sn": SN,
            "groups": [NIGHT_GROUP, DISCHARGE_GROUP],
            "enable_scheduler": True,
        }
    ]
    assert state(hass, "sensor", "active_schedule").state == (
        "ForceCharge 02:00-04:59, ForceDischarge 17:00-18:59"
    )


async def test_set_schedule_accepts_a_max_soc(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    await setup_bridge(hass, fox)
    await call(
        hass,
        SERVICE_SET_SCHEDULE,
        {"groups": [{"start": "02:00", "end": "04:59", "max_soc": 80}]},
    )
    assert fox.groups == [{**NIGHT_GROUP, "maxSoc": 80}]


async def test_set_schedule_can_leave_the_scheduler_off(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    await setup_bridge(hass, fox)
    await call(
        hass,
        SERVICE_SET_SCHEDULE,
        {"groups": [{"start": "02:00", "end": "04:59"}], "enable": False},
    )
    assert fox.service_calls("set_schedule")[0]["enable_scheduler"] is False
    assert fox.groups == [NIGHT_GROUP]
    assert fox.flag_enabled is False


async def test_set_schedule_rejects_overlapping_groups(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    await setup_bridge(hass, fox)
    with pytest.raises(ServiceValidationError, match="overlap"):
        await call(
            hass,
            SERVICE_SET_SCHEDULE,
            {
                "groups": [
                    {"start": "02:00", "end": "05:00"},
                    {"start": "05:00", "end": "06:00"},
                ]
            },
        )
    assert fox.service_calls("set_schedule") == []


async def test_set_schedule_is_refused_while_the_bridge_is_live(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    await setup_bridge(hass, fox, LIVE)
    with pytest.raises(ServiceValidationError, match="dry run"):
        await call(
            hass, SERVICE_SET_SCHEDULE, {"groups": [{"start": "02:00", "end": "04:59"}]}
        )
    assert fox.service_calls("set_schedule") == []
