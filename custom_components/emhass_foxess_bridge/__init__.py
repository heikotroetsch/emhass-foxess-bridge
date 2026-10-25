"""
EMHASS FoxESS Bridge.

Day-ahead battery optimisation in Home Assistant for Enpal solar systems
(FoxESS H3 hybrid inverter and battery) on the Octopus Energy Germany
dynamic tariff: EMHASS plans the charging, this integration applies the
plan to the inverter's mode scheduler. The FoxESS cloud is reached through
the ha-foxess-openapi integration, which owns the API key and the live
sensors. Any FoxESS inverter with the mode scheduler and any price source
EMHASS can read will work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv

from .backend import (
    OpenApiServiceBackend,
    async_resolve_device,
    async_services_available,
)
from .const import CONF_DEVICE_ID, DOMAIN
from .data import BridgeRuntimeData
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
    """Resolve the inverter behind ha-foxess-openapi and start the apply loop."""
    if not async_services_available(hass):
        msg = "The foxess_openapi services are not registered yet"
        raise ConfigEntryNotReady(msg)
    device = async_resolve_device(hass, entry.data[CONF_DEVICE_ID])
    if device is None:
        msg = (
            "The FoxESS device of this entry no longer exists in ha-foxess-openapi. "
            "Remove the bridge and add it again."
        )
        raise ConfigEntryError(msg)

    backend = OpenApiServiceBackend(hass, device)
    schedule = ScheduleCoordinator(hass, config_entry=entry, backend=backend)
    entry.runtime_data = BridgeRuntimeData(
        device=device, backend=backend, schedule=schedule
    )
    await schedule.async_config_entry_first_refresh()
    entry.async_on_unload(schedule.async_start())
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BridgeConfigEntry) -> bool:
    """Unload the platforms."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
