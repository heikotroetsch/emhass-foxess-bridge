"""Tests for entry setup, retry and unload."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr

from custom_components.emhass_foxess_bridge.const import DOMAIN

from .fake_foxess_openapi import SN, FakeFoxessOpenApi, eid, setup_bridge

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_setup_creates_a_bridge_device_linked_to_the_inverter(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    entry = await setup_bridge(hass, fox)
    assert entry.state is ConfigEntryState.LOADED

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, SN), entry.entry_id
    )
    assert device is not None
    assert device.via_device_id == fox.device_id
    assert device.name == "EMHASS bridge FoxESS Test Plant"
    for platform, key in (
        ("sensor", "planned_schedule"),
        ("sensor", "active_schedule"),
        ("sensor", "apply_result"),
        ("sensor", "last_apply"),
        ("binary_sensor", "plan_stale"),
        ("switch", "bridge_enabled"),
        ("switch", "dry_run"),
    ):
        assert hass.states.get(eid(hass, platform, key)) is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_retries_until_foxess_openapi_services_exist(
    hass: HomeAssistant,
) -> None:
    fake = FakeFoxessOpenApi(hass)
    await fake.async_setup(register_services=False)
    entry = await setup_bridge(hass, fake)
    assert entry.state is ConfigEntryState.SETUP_RETRY
