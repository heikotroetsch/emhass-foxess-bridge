"""Tests for the manual services: apply_plan, clear_schedule, set_schedule."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.emhass_foxess_bridge.api import (
    PATH_SCHEDULER_ENABLE,
    PATH_SCHEDULER_SET,
)
from custom_components.emhass_foxess_bridge.const import (
    CONF_ENABLED,
    DOMAIN,
    SERVICE_APPLY_PLAN,
    SERVICE_CLEAR_SCHEDULE,
    SERVICE_SET_SCHEDULE,
)

from .fake_fox import (
    LIVE,
    NIGHT_GROUP,
    PLAN_TIME,
    SN,
    FakeFoxCloud,
    publish_plan,
    settle,
    setup_bridge,
    state,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant


async def call(hass: HomeAssistant, service: str, data: dict | None = None) -> None:
    await hass.services.async_call(DOMAIN, service, data or {}, blocking=True)
    await hass.async_block_till_done()


async def test_apply_plan_runs_the_loop_now(
    hass: HomeAssistant,
    fox: FakeFoxCloud,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    publish_plan(hass, load_fixture)
    entry = await setup_bridge(hass, LIVE)
    await settle(hass, freezer)
    reads = fox.reads()

    await call(hass, SERVICE_APPLY_PLAN, {"config_entry_id": entry.entry_id})
    assert fox.reads() == reads + 1
    assert state(hass, "sensor", "apply_result").state == "unchanged"


async def test_services_default_to_every_loaded_entry(
    hass: HomeAssistant, fox: FakeFoxCloud
) -> None:
    await setup_bridge(hass)
    reads = fox.reads()
    await call(hass, SERVICE_APPLY_PLAN)
    assert fox.reads() == reads + 1


async def test_unknown_entry_id_is_rejected(
    hass: HomeAssistant, fox: FakeFoxCloud
) -> None:
    await setup_bridge(hass)
    with pytest.raises(ServiceValidationError):
        await call(hass, SERVICE_APPLY_PLAN, {"config_entry_id": "does-not-exist"})


async def test_clear_schedule_disables_the_scheduler_and_the_bridge(
    hass: HomeAssistant,
    fox: FakeFoxCloud,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    fox.groups = [NIGHT_GROUP]
    fox.flag_enabled = True
    publish_plan(hass, load_fixture)
    entry = await setup_bridge(hass, LIVE)
    await settle(hass, freezer)
    assert state(hass, "sensor", "active_schedule").state == "ForceCharge 02:00-04:59"

    await call(hass, SERVICE_CLEAR_SCHEDULE)

    assert fox.writes(PATH_SCHEDULER_SET) == [{"deviceSN": SN, "enable": 0}]
    assert entry.options[CONF_ENABLED] is False
    assert state(hass, "switch", "bridge_enabled").state == "off"
    assert state(hass, "sensor", "active_schedule").state == "none"
    assert fox.writes(PATH_SCHEDULER_ENABLE) == [], "the plan must not be re-applied"


async def test_set_schedule_writes_raw_groups(
    hass: HomeAssistant, fox: FakeFoxCloud
) -> None:
    await setup_bridge(hass)
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
    assert fox.writes(PATH_SCHEDULER_ENABLE) == [
        {
            "deviceSN": SN,
            "groups": [
                NIGHT_GROUP,
                {
                    "enable": 1,
                    "startHour": 17,
                    "startMinute": 0,
                    "endHour": 18,
                    "endMinute": 59,
                    "workMode": "ForceDischarge",
                    "minSocOnGrid": 10,
                    "fdSoc": 20,
                    "fdPwr": 3000,
                },
            ],
        }
    ]
    assert fox.writes(PATH_SCHEDULER_SET) == [{"deviceSN": SN, "enable": 1}]
    assert state(hass, "sensor", "active_schedule").state == (
        "ForceCharge 02:00-04:59, ForceDischarge 17:00-18:59"
    )


async def test_set_schedule_can_leave_the_scheduler_off(
    hass: HomeAssistant, fox: FakeFoxCloud
) -> None:
    await setup_bridge(hass)
    await call(
        hass,
        SERVICE_SET_SCHEDULE,
        {"groups": [{"start": "02:00", "end": "04:59"}], "enable": False},
    )
    assert fox.writes(PATH_SCHEDULER_SET) == [{"deviceSN": SN, "enable": 0}]
    assert fox.groups == [NIGHT_GROUP]


async def test_set_schedule_rejects_overlapping_groups(
    hass: HomeAssistant, fox: FakeFoxCloud
) -> None:
    await setup_bridge(hass)
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
    assert fox.writes(PATH_SCHEDULER_ENABLE) == []


async def test_set_schedule_is_refused_while_the_bridge_is_live(
    hass: HomeAssistant, fox: FakeFoxCloud
) -> None:
    await setup_bridge(hass, LIVE)
    with pytest.raises(ServiceValidationError, match="dry run"):
        await call(
            hass, SERVICE_SET_SCHEDULE, {"groups": [{"start": "02:00", "end": "04:59"}]}
        )
    assert fox.writes(PATH_SCHEDULER_ENABLE) == []
