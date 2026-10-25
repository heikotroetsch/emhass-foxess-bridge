"""
EMHASS FoxESS Bridge.

Applies the battery plan computed by EMHASS to a FoxESS inverter through the
FoxESS Cloud Open API mode scheduler, and exposes the inverter's live values.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FoxOpenApiClient
from .const import (
    CONF_API_KEY,
    CONF_DEVICE_SN,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    LOGGER,
)
from .coordinator import BridgeCoordinator
from .data import BridgeRuntimeData

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import BridgeConfigEntry

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: BridgeConfigEntry) -> bool:
    """Create the API client and coordinator, then load the platforms."""
    client = FoxOpenApiClient(entry.data[CONF_API_KEY], async_get_clientsession(hass))
    device_sn: str = entry.data[CONF_DEVICE_SN]
    device_detail = await client.async_get_device_detail(device_sn)

    coordinator = BridgeCoordinator(
        hass,
        LOGGER,
        config_entry=entry,
        name=DOMAIN,
        update_interval=timedelta(minutes=DEFAULT_SCAN_INTERVAL_MINUTES),
        client=client,
        device_sn=device_sn,
    )
    entry.runtime_data = BridgeRuntimeData(
        client=client,
        coordinator=coordinator,
        device_sn=device_sn,
        device_detail=device_detail,
    )
    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BridgeConfigEntry) -> bool:
    """Unload the platforms."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
