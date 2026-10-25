"""Diagnostics download for issue reports; secrets and serials are redacted."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN

from .const import (
    CONF_API_KEY,
    CONF_DEVICE_SN,
    CONF_EMHASS_ENTITY,
    CONF_EMHASS_GRID_ENTITY,
)

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant

    from .data import BridgeConfigEntry

TO_REDACT = {
    CONF_API_KEY,
    CONF_DEVICE_SN,
    "deviceSN",
    "moduleSN",
    "stationID",
    "stationName",
    "batterySN",
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BridgeConfigEntry
) -> dict[str, Any]:
    """Return config, inverter facts, loop state and the EMHASS plan as published."""
    runtime = entry.runtime_data
    schedule = runtime.schedule.data

    emhass: dict[str, Any] = {}
    for key in (CONF_EMHASS_ENTITY, CONF_EMHASS_GRID_ENTITY):
        entity_id = entry.data.get(key)
        state = hass.states.get(entity_id) if entity_id else None
        if state is None:
            continue
        emhass[entity_id] = {
            "state": state.state,
            "available": state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN),
            "last_updated": state.last_updated.isoformat(),
            "attributes": dict(state.attributes),
        }

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "device": async_redact_data(runtime.device_detail, TO_REDACT),
        "min_soc_on_grid": runtime.min_soc_on_grid,
        "realtime": runtime.coordinator.data,
        "schedule": {
            "planned": [g.to_api() for g in schedule.planned],
            "active": [g.to_api() for g in schedule.active],
            "scheduler_enabled": schedule.scheduler_enabled,
            "plan_stale": schedule.plan_stale,
            "stale_reason": schedule.stale_reason,
            "plan_updated_at": _iso(schedule.plan_updated_at),
            "last_apply_at": _iso(schedule.last_apply_at),
            "last_result": schedule.last_result.value,
            "last_detail": schedule.last_detail,
            "writes_blocked_until": _iso(schedule.writes_blocked_until),
        },
        "emhass": emhass,
    }
