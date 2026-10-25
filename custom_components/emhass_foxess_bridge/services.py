"""Manual services: apply_plan, clear_schedule and set_schedule."""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .backend import FoxBackendError
from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_ENABLE,
    ATTR_GROUPS,
    DOMAIN,
    SERVICE_APPLY_PLAN,
    SERVICE_CLEAR_SCHEDULE,
    SERVICE_SET_SCHEDULE,
)
from .schedule import (
    MAX_GROUPS,
    MAX_SOC,
    MIN_SOC,
    ScheduleError,
    SchedulerGroup,
    WorkMode,
    validate_groups,
)

if TYPE_CHECKING:
    from .data import BridgeConfigEntry

ENTRY_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string})

_SOC = vol.All(vol.Coerce(int), vol.Range(min=MIN_SOC, max=MAX_SOC))
GROUP_SCHEMA = vol.Schema(
    {
        vol.Required("start"): cv.time,
        vol.Required("end"): cv.time,
        vol.Optional("work_mode", default=WorkMode.FORCE_CHARGE.value): vol.In(
            [mode.value for mode in WorkMode]
        ),
        vol.Optional("min_soc_on_grid", default=MIN_SOC): _SOC,
        vol.Optional("fd_soc", default=MIN_SOC): _SOC,
        vol.Optional("fd_pwr", default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional("max_soc", default=MAX_SOC): _SOC,
    }
)
SET_SCHEDULE_SCHEMA = ENTRY_SCHEMA.extend(
    {
        vol.Required(ATTR_GROUPS): vol.All(
            cv.ensure_list, [GROUP_SCHEMA], vol.Length(min=1, max=MAX_GROUPS)
        ),
        vol.Optional(ATTR_ENABLE, default=True): cv.boolean,
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the three services."""
    hass.services.async_register(
        DOMAIN, SERVICE_APPLY_PLAN, _apply_plan, schema=ENTRY_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_SCHEDULE, _clear_schedule, schema=ENTRY_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_SCHEDULE, _set_schedule, schema=SET_SCHEDULE_SCHEMA
    )


def _entries_for(call: ServiceCall) -> list[BridgeConfigEntry]:
    """Resolve the target entries: the given one, or every loaded one."""
    hass = call.hass
    entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
    if entry_id is None:
        entries = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.state is ConfigEntryState.LOADED
        ]
        if not entries:
            msg = "No EMHASS FoxESS Bridge entry is loaded"
            raise ServiceValidationError(msg)
        return entries
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        msg = f"No EMHASS FoxESS Bridge entry with id {entry_id}"
        raise ServiceValidationError(msg)
    if entry.state is not ConfigEntryState.LOADED:
        msg = f"Entry {entry.title} is not loaded"
        raise ServiceValidationError(msg)
    return [entry]


async def _apply_plan(call: ServiceCall) -> None:
    for entry in _entries_for(call):
        await entry.runtime_data.schedule.async_refresh()


async def _clear_schedule(call: ServiceCall) -> None:
    for entry in _entries_for(call):
        try:
            await entry.runtime_data.schedule.async_clear()
        except FoxBackendError as exc:
            msg = f"Could not disable the scheduler on {entry.title}: {exc}"
            raise HomeAssistantError(msg) from exc


async def _set_schedule(call: ServiceCall) -> None:
    groups = tuple(
        SchedulerGroup(
            start=raw["start"],
            end=raw["end"],
            work_mode=WorkMode(raw["work_mode"]),
            min_soc_on_grid=raw["min_soc_on_grid"],
            fd_soc=raw["fd_soc"],
            fd_pwr=raw["fd_pwr"],
            max_soc=raw["max_soc"],
        )
        for raw in call.data[ATTR_GROUPS]
    )
    try:
        validate_groups(groups)
    except ScheduleError as exc:
        raise ServiceValidationError(str(exc)) from exc

    for entry in _entries_for(call):
        schedule = entry.runtime_data.schedule
        if schedule.bridge_enabled and not schedule.dry_run:
            msg = (
                f"{entry.title} is live and would overwrite a manual schedule within "
                "15 minutes. Switch the bridge off or enable dry run first."
            )
            raise ServiceValidationError(msg)
        try:
            await schedule.async_write_groups(groups, enable=call.data[ATTR_ENABLE])
        except FoxBackendError as exc:
            msg = f"Could not write the schedule to {entry.title}: {exc}"
            raise HomeAssistantError(msg) from exc
