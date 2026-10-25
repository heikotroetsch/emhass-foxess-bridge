"""Tests for the ha-foxess-openapi service backend."""

from __future__ import annotations

from datetime import time
from typing import TYPE_CHECKING

import pytest

from custom_components.emhass_foxess_bridge.backend import (
    FoxBackendError,
    FoxBackendRateLimitError,
    OpenApiServiceBackend,
    async_resolve_device,
    async_services_available,
)
from custom_components.emhass_foxess_bridge.schedule import SchedulerGroup, WorkMode

from .fake_foxess_openapi import (
    DEVICE_NAME,
    NIGHT_GROUP,
    RATE_LIMIT_MESSAGE,
    SN,
    FakeFoxessOpenApi,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

NIGHT = SchedulerGroup(time(2, 0), time(4, 59), WorkMode.FORCE_CHARGE)


def backend_for(hass: HomeAssistant, fox: FakeFoxessOpenApi) -> OpenApiServiceBackend:
    device = async_resolve_device(hass, fox.device_id)
    assert device is not None
    return OpenApiServiceBackend(hass, device)


async def test_services_are_detected(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    assert async_services_available(hass) is True


async def test_missing_services_are_detected(hass: HomeAssistant) -> None:
    fake = FakeFoxessOpenApi(hass)
    await fake.async_setup(register_services=False)
    assert async_services_available(hass) is False


async def test_device_resolves_serial_name_and_entities(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    device = async_resolve_device(hass, fox.device_id)
    assert device is not None
    assert device.serial == SN
    assert device.name == DEVICE_NAME
    assert device.entity_ids["minSocOnGrid"] == fox.entity_ids["minSocOnGrid"]
    assert device.entity_ids["schedule"] == fox.entity_ids["schedule"]
    assert device.entity_ids["api_remaining"] == fox.entity_ids["api_remaining"]
    assert device.entity_ids["scheduler_enable"] == fox.entity_ids["scheduler_enable"]


async def test_unknown_device_resolves_to_none(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    assert async_resolve_device(hass, "no-such-device") is None


async def test_read_parses_groups_flag_min_soc_and_quota(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    fox.groups = [NIGHT_GROUP]
    fox.flag_enabled = True
    fox.min_soc_on_grid = 15
    fox.api_remaining = 987
    fox.publish()

    reading = await backend_for(hass, fox).async_read()

    assert reading.groups == (NIGHT,)
    assert reading.scheduler_enabled is True
    assert reading.min_soc_on_grid == 15
    assert reading.api_remaining == 987  # as the quota sensor reported it
    assert fox.service_calls("get_schedule") == [{"device_sn": SN}]


async def test_master_flag_comes_from_the_response_first(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    fox.flag_enabled = True
    hass.states.async_set(fox.entity_ids["scheduler_enable"], "off")  # stale entity
    reading = await backend_for(hass, fox).async_read()
    assert reading.scheduler_enabled is True


async def test_without_flag_in_the_response_the_switch_entity_is_used(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    fox.response_has_enable = False
    fox.flag_enabled = True
    fox.publish()
    reading = await backend_for(hass, fox).async_read()
    assert reading.scheduler_enabled is True


async def test_unknown_switch_state_falls_back_to_the_schedule_sensor(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    fox.response_has_enable = False
    fox.flag_enabled = True
    fox.publish()
    hass.states.async_set(fox.entity_ids["scheduler_enable"], "unavailable")
    reading = await backend_for(hass, fox).async_read()
    assert reading.scheduler_enabled is True


async def test_write_sends_v1_groups_and_enables_the_scheduler(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    await backend_for(hass, fox).async_write((NIGHT,), enable=True)
    assert fox.service_calls("set_schedule") == [
        {"device_sn": SN, "groups": [NIGHT_GROUP], "enable_scheduler": True}
    ]
    assert fox.groups == [NIGHT_GROUP]
    assert fox.flag_enabled is True


async def test_write_can_leave_the_scheduler_off(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    await backend_for(hass, fox).async_write((NIGHT,), enable=False)
    assert fox.service_calls("set_schedule")[0]["enable_scheduler"] is False
    assert fox.flag_enabled is False


async def test_disable_calls_disable_scheduler(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    fox.flag_enabled = True
    await backend_for(hass, fox).async_disable()
    assert fox.service_calls("disable_scheduler") == [{"device_sn": SN}]
    assert fox.flag_enabled is False


async def test_rate_limit_text_becomes_a_rate_limit_error(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    fox.fail_writes_with = RATE_LIMIT_MESSAGE
    with pytest.raises(FoxBackendRateLimitError):
        await backend_for(hass, fox).async_write((NIGHT,), enable=True)


async def test_other_errors_become_backend_errors(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    fox.fail_reads_with = "FoxESS API error 41200: bad request"
    with pytest.raises(FoxBackendError, match="41200"):
        await backend_for(hass, fox).async_read()


async def test_missing_service_is_a_backend_error(hass: HomeAssistant) -> None:
    fake = FakeFoxessOpenApi(hass)
    await fake.async_setup(register_services=False)
    device = async_resolve_device(hass, fake.device_id)
    assert device is not None
    with pytest.raises(FoxBackendError):
        await OpenApiServiceBackend(hass, device).async_read()
