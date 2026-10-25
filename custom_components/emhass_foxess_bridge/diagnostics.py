"""Diagnostics download for issue reports; the inverter serial is redacted."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics.const import REDACTED
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN

from .const import CONF_EMHASS_ENTITY, CONF_EMHASS_GRID_ENTITY

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant

    from .data import BridgeConfigEntry


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _snapshot(hass: HomeAssistant, entity_id: str | None) -> dict[str, Any] | None:
    state = hass.states.get(entity_id) if entity_id else None
    if state is None:
        return None
    return {
        "state": state.state,
        "available": state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN),
        "last_updated": state.last_updated.isoformat(),
        "attributes": dict(state.attributes),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BridgeConfigEntry
) -> dict[str, Any]:
    """Return config, resolved inverter, loop state and the plans as published."""
    runtime = entry.runtime_data
    device = runtime.device
    schedule = runtime.schedule.data

    emhass = {
        entity_id: snapshot
        for key in (CONF_EMHASS_ENTITY, CONF_EMHASS_GRID_ENTITY)
        if (entity_id := entry.data.get(key))
        and (snapshot := _snapshot(hass, entity_id)) is not None
    }
    foxess = {
        key: snapshot
        for key, entity_id in device.entity_ids.items()
        if (snapshot := _snapshot(hass, entity_id)) is not None
    }

    return {
        "entry": {"data": dict(entry.data), "options": dict(entry.options)},
        "device": {
            "serial": REDACTED,
            "name": device.name.replace(device.serial, REDACTED),
            "entity_ids": dict(device.entity_ids),
        },
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
            "min_soc_on_grid": schedule.min_soc_on_grid,
            "api_remaining": schedule.api_remaining,
        },
        "foxess_openapi": foxess,
        "emhass": emhass,
    }
