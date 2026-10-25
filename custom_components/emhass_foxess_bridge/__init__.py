"""
EMHASS FoxESS Bridge.

Applies the battery plan computed by EMHASS to a FoxESS inverter through the
FoxESS Cloud Open API mode scheduler, and exposes the inverter's live values.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FoxApiError, FoxOpenApiClient
from .const import (
    CONF_API_KEY,
    CONF_DEVICE_SN,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    LOGGER,
)
from .coordinator import BridgeCoordinator
from .data import BridgeRuntimeData
from .schedule import MIN_SOC
from .schedule_coordinator import ScheduleCoordinator
from .services import async_setup_services

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType

    from .data import BridgeConfigEntry

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:  # noqa: ARG001
    """Register the manual services once per Home Assistant instance."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BridgeConfigEntry) -> bool:
    """Create the API client and both coordinators, then load the platforms."""
    client = FoxOpenApiClient(entry.data[CONF_API_KEY], async_get_clientsession(hass))
    device_sn: str = entry.data[CONF_DEVICE_SN]
    device_detail = await client.async_get_device_detail(device_sn)
    min_soc_on_grid = await _read_min_soc_on_grid(client, device_sn)

    coordinator = BridgeCoordinator(
        hass,
        LOGGER,
        config_entry=entry,
        name=DOMAIN,
        update_interval=timedelta(minutes=DEFAULT_SCAN_INTERVAL_MINUTES),
        client=client,
        device_sn=device_sn,
    )
    schedule = ScheduleCoordinator(
        hass,
        config_entry=entry,
        client=client,
        device_sn=device_sn,
        min_soc_on_grid=min_soc_on_grid,
    )
    entry.runtime_data = BridgeRuntimeData(
        client=client,
        coordinator=coordinator,
        schedule=schedule,
        device_sn=device_sn,
        device_detail=device_detail,
        min_soc_on_grid=min_soc_on_grid,
    )
    await coordinator.async_config_entry_first_refresh()
    await schedule.async_config_entry_first_refresh()
    entry.async_on_unload(schedule.async_start())
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _read_min_soc_on_grid(client: FoxOpenApiClient, device_sn: str) -> int:
    """Read the inverter's minSocOnGrid so our groups keep the owner's setting."""
    try:
        soc = await client.async_get_battery_soc(device_sn)
    except FoxApiError as exc:
        LOGGER.warning("Could not read minSocOnGrid, using %d%%: %s", MIN_SOC, exc)
        return MIN_SOC
    return int(soc.get("minSocOnGrid", MIN_SOC))


async def async_unload_entry(hass: HomeAssistant, entry: BridgeConfigEntry) -> bool:
    """Unload the platforms."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
